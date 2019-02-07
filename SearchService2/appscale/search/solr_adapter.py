"""
Implements Search API functionality using Solr as a backend.

Interaction between APIMethods and SolrAdapter is done using objects
described in appscale.search.models.
"""

import collections
import logging
import re
from datetime import datetime

from tornado import gen

from appscale.search import query_converter
from appscale.search.constants import (
  SOLR_ZK_ROOT, SUPPORTED_LANGUAGES, UnknownFieldTypeException
)
from appscale.search.models import (
  Field, ScoredDocument, SearchResult, SolrIndexSchemaInfo, SolrSchemaFieldInfo
)
from appscale.search.solr_api import SolrAPI

logger = logging.getLogger(__name__)

# The regex helps to identify no supported sort expression
EXPRESSION_SIGN = re.compile('[-+*/,()]')


class SolrAdapter(object):
  """
  SolrAdapter implements Google Search API methods using Solr as a backend.
  Solr adapter is used by APIMethods which
  wraps it with protocol buffer conversion.
  """

  def __init__(self, zk_client):
    """ Initialises an instance of SolrAdapter.
    In particular it creates SolrAPI object which helps to perform
    basic operation with Solr API.

    Args:
      zk_client: An instance of kazoo.client.KazooClient.
    """
    self.solr = SolrAPI(zk_client, SOLR_ZK_ROOT)

  @gen.coroutine
  def index_documents(self, app_id, namespace, index_name, documents):
    """ Puts specified documents into the index (asynchronously).

    Args:
      app_id: a str representing Application ID.
      namespace: a str representing GAE namespace or None.
      index_name: a str representing name of Search API index.
      documents: a list of documents to put into the index.
    """
    collection = get_collection_name(app_id, namespace, index_name)
    solr_documents = [_to_solr_document(doc) for doc in documents]
    yield self.solr.put_documents(collection, solr_documents)

  @gen.coroutine
  def delete_documents(self, app_id, namespace, index_name, ids):
    """ Deletes documents with specified IDs from the index (asynchronously).

    Args:
      app_id: a str representing Application ID.
      namespace: a str representing GAE namespace or None.
      index_name: a str representing name of Search API index.
      ids: a list of document IDs to delete.
    """
    collection = get_collection_name(app_id, namespace, index_name)
    yield self.solr.delete_documents(collection, ids)

  @gen.coroutine
  def list_documents(self, app_id, namespace, index_name, start_doc_id,
                     include_start_doc, limit, keys_only):
    """ Retrieves up to limit documents starting from start_doc_id
    and converts it from Solr format to unified Search API documents.

    Args:
      app_id: a str representing Application ID.
      namespace: a str representing GAE namespace or None.
      index_name: a str representing name of Search API index.
      start_doc_id: a str - doc ID to start from.
      include_start_doc: a bool indicating if the start doc should be included.
      limit: a int - max number of documents to retrieve.
      keys_only: a bool indicating if only document keys should be returned.
    Return (asynchronously):
      A list of models.ScoredDocument.
    """
    collection = get_collection_name(app_id, namespace, index_name)

    if start_doc_id:
      # Apply range filter to ID
      left_bracket = '[' if include_start_doc else '{'
      solr_filter_query = 'id:{}{} TO *]'.format(left_bracket, start_doc_id)
    else:
      solr_filter_query = None
    # Order by ID
    solr_sort_fields = ['id asc']

    solr_projection_fields = None
    if keys_only:
      # Skip everything but ID
      solr_projection_fields = ['id']

    # Use *:* to match any document
    solr_search_result = yield self.solr.query_documents(
      collection=collection, query='*:*', filter_=solr_filter_query,
      limit=limit, fields=solr_projection_fields, sort=solr_sort_fields
    )
    docs = [_from_solr_document(solr_doc)
            for solr_doc in solr_search_result.documents]
    raise gen.Return(docs)

  @gen.coroutine
  def query(self, app_id, namespace, index_name, query, projection_fields,
            sort_expressions, limit, offset, cursor, keys_only):
    """ Retrieves documents which matches query from Solr collection
    and converts it to unified documents.

    Args:
      app_id: a str representing Application ID.
      namespace: a str representing GAE namespace or None.
      index_name: a str representing name of Search API index.
      query: a str containing Search API query.
      projection_fields: a list of field names to return in results.
      sort_expressions: a list of sort expressions, e.g.: ("field1", "asc").
      limit: an int specifying maximum number of results to return.
      offset: an int specifying number of first document to skip.
      cursor: a str representing query cursor.
      keys_only: a bool indicating if only document IDs should be returned.
    Returns (asynchronously):
      An instance of models.SearchResult.
    """
    # TODO: Cache schema info or store it in watched zookeeper node.
    index_schema = yield self._get_schema_info(app_id, namespace, index_name)
    # Convert Search API query to Solr query with a list of fields to search.
    query_options = query_converter.prepare_solr_query(
      query, index_schema.fields, index_schema.grouped_fields
    )

    # Process projection_fields
    if projection_fields:
      solr_projection_fields = ['id', 'rank', 'language']
      for gae_name in projection_fields:
        # (1) In GAE fields with different type can have the same name,
        # in Solr they are stored as fields with different name (type suffix).
        solr_projection_fields += [
          solr_field.solr_name for solr_field in
          index_schema.grouped_fields[gae_name]
        ]
    elif keys_only:
      # Skip everything but ID.
      solr_projection_fields = ['id', 'rank', 'language']
    else:
      # Return all fields.
      solr_projection_fields = None

    solr_sort_fields = []
    if sort_expressions:
      for sort_expression, direction in sort_expressions:
        if EXPRESSION_SIGN.search(sort_expression):
          logger.warning('Sort expression currently supports only field names')
          continue
        fields_group = index_schema.grouped_fields[sort_expression]
        if not fields_group:
          logger.warning('SortExpression: Could not find field "{}"'
                         .format(sort_expression))
          continue
        # There can be multiple fields with the same GAE name [*1],
        # so let's pick most "popular" field of those.
        biggest_field = max(fields_group, key=lambda v: v.docs_number)
        solr_sort_expr = '{} {}'.format(biggest_field.solr_name, direction)
        solr_sort_fields.append(solr_sort_expr)
    if not solr_sort_fields:
      solr_sort_fields = ['rank desc']

    solr_result = yield self.solr.query_documents(
      collection=index_schema.collection,
      query=query_options.query_string, offset=offset, limit=limit,
      cursor=cursor, fields=solr_projection_fields, sort=solr_sort_fields,
      def_type=query_options.def_type, query_fields=query_options.query_fields
    )
    docs = [_from_solr_document(solr_doc)
            for solr_doc in solr_result.documents]
    facets = [_from_solr_facet(solr_facet)
              for solr_facet in solr_result.facets or []]

    result = SearchResult(
      num_found=solr_result.num_found, scored_documents=docs,
      cursor=cursor, facets=facets
    )
    raise gen.Return(result)

  @gen.coroutine
  def _get_schema_info(self, app_id, namespace, gae_index_name):
    """ Retrieves information about schema of Solr collection
    corresponding to Search API index.

    Args:
      app_id: a str representing Application ID.
      namespace: a str representing GAE namespace or None.
      gae_index_name: a str representing name of Search API index.
    Returns (asynchronously):
      An instance of SolrIndexSchemaInfo.
    """
    collection = get_collection_name(app_id, namespace, gae_index_name)
    solr_schema_info = yield self.solr.get_schema_info(collection)
    fields_info = solr_schema_info['fields']
    id_field = SolrSchemaFieldInfo(
      solr_name='id', gae_name='doc_id', type=Field.Type.ATOM,
      language=None, docs_number=fields_info.get('id', {}).get('docs', 0)
    )
    rank_field = SolrSchemaFieldInfo(
        solr_name='rank', gae_name='rank', type=Field.Type.NUMBER,
        language=None, docs_number=fields_info.get('rank', {}).get('docs', 0)
    )
    fields = [id_field, rank_field]
    grouped_fields = collections.defaultdict(list)
    grouped_fields['doc_id'] = [id_field]
    grouped_fields['rank'] = [rank_field]
    for solr_field_name, info in fields_info.items():
      try:
        gae_name, type_, language = parse_solr_field_name(solr_field_name)
      except ValueError:
        continue
      schema_field = SolrSchemaFieldInfo(
        solr_name=solr_field_name, gae_name=gae_name, type=type_,
        language=language, docs_number=info.get('docs', 0)
      )
      fields.append(schema_field)
      grouped_fields[gae_name].append(schema_field)

    index_info = solr_schema_info['index']
    raise gen.Return(SolrIndexSchemaInfo(
      app_id=app_id,
      namespace=namespace,
      gae_index_name=gae_index_name,
      collection=collection,
      docs_number=index_info['numDocs'],
      heap_usage=index_info['indexHeapUsageBytes'],
      size_in_bytes=index_info['segmentsFileSizeInBytes'],
      fields=fields,
      grouped_fields=grouped_fields
    ))


def get_collection_name(app_id, namespace, gae_index_name):
  return u'appscale_{}_{}_{}'.format(app_id, namespace, gae_index_name)


def _to_solr_document(document):
  """ Converts an instance of models.Document
  to dictionary in format supported by Solr.

  Args:
    document: an instance of models.Document.
  Returns:
    A dictionary in Solr format.
  """
  solr_doc = collections.defaultdict(list)
  solr_doc['id'] = document.doc_id
  solr_doc['rank'] = document.rank
  solr_doc['language'] = document.language or ''

  for field in document.fields:

    lang_suffix = ''
    lang = field.language or document.language
    if lang in SUPPORTED_LANGUAGES:
      lang_suffix = '_{}'.format(lang)
    elif lang is not None:
      logger.warning('Language "{}" is not supported'.format(lang))

    if field.type == Field.Type.TEXT:
      solr_field_name = '{}_{}{}'.format(field.name, 'txt', lang_suffix)
      solr_doc[solr_field_name].append(field.value)
    elif field.type == Field.Type.HTML:
      # TODO implement HTML analyser
      solr_field_name = '{}_{}{}'.format(field.name, 'txt', lang_suffix)
      solr_doc[solr_field_name].append(field.value)
    elif field.type == Field.Type.ATOM:
      solr_field_name = '{}_{}'.format(field.name, 'atom')
      solr_doc[solr_field_name].append(field.value)
    elif field.type == Field.Type.NUMBER:
      solr_field_name = '{}_{}'.format(field.name, 'number')
      solr_doc[solr_field_name].append(field.value)
    elif field.type == Field.Type.DATE:
      solr_field_name = '{}_{}'.format(field.name, 'date')
      datetime_str = field.value.strftime('%Y-%m-%dT%H:%M:%SZ')
      solr_doc[solr_field_name].append(datetime_str)
    elif field.type == Field.Type.GEO:
      solr_field_name = '{}_{}'.format(field.name, 'geo')
      geo_str = '{},{}'.format(field.value[0], field.value[1])
      solr_doc[solr_field_name].append(geo_str)
    else:
      raise UnknownFieldTypeException(
        "A document contains a field of unknown type: {}"
        .format(field.type)
      )

  # TODO facets:
  #   atom values should be saved to *_atom_facet_value
  #   lowercased value should be saved to *_atom_facet
  return solr_doc


def _from_solr_document(solr_doc):
  """ Converts solr_doc to models.ScoredDocument.

  Args:
    solr_doc: a dict containing a document as returned from Solr.
  Returns:
    An instance of models.ScoredDocument.
  """
  fields = []
  for solr_field_name, values in solr_doc.items():
    # Extract field name, type and language from solr_field_name
    try:
      name, type_, language = parse_solr_field_name(solr_field_name)
    except ValueError:
      continue

    # Convert string values to python types if applicable
    if type_ == Field.Type.DATE:
      values = [
        datetime.strptime(datetime_str, '%Y-%m-%dT%H:%M:%SZ')
        for datetime_str in values
      ]
    elif type_ == Field.Type.GEO:
      values = [
        tuple([float(lat_or_lng_str) for lat_or_lng_str in geo_str.split(',')])
        for geo_str in values
      ]

    # Add field for each value
    for value in values:
      field = Field(type=type_, name=name, value=value, language=language)
      fields.append(field)

  return ScoredDocument(
    doc_id=solr_doc['id'],
    fields=fields,
    language=solr_doc['language'],
    sort_scores=None,   # Is not supported yet
    expressions=None,   # Is not supported yet
    cursor=None,        # Is not supported yet
    rank=solr_doc['rank']
  )


def _from_solr_facet(solr_facet):
  # TODO
  pass


_FIELD_TYPE = 'atom|number|date|geo|txt'
_FIELD_TYPE_MAPPING = {
  'atom': Field.Type.ATOM,
  'number': Field.Type.NUMBER,
  'date': Field.Type.DATE,
  'geo': Field.Type.GEO,
  'txt': Field.Type.TEXT
}
_LANGUAGE = '|'.join(SUPPORTED_LANGUAGES)
_FIELD_NAME_PATTERN = re.compile(
  '^(?P<field_name>[\w_]+?)_(?P<solr_type>{})(_(?P<language>{}))?$'
  .format(_FIELD_TYPE, _LANGUAGE)
)


def parse_solr_field_name(solr_field_name):
  """ Extracts GAE field name, field type and language
  from Solr field name.

  Args:
    solr_field_name: a str representing field name of Solr collection.
  Returns:
    A tuple of (<field_name>, <field_type>, <language>).
  """
  match = _FIELD_NAME_PATTERN.match(solr_field_name)
  if not match:
    raise ValueError('Provided Solr field does not belong to Search Service')
  field_name = match.group('field_name')
  solr_type = match.group('solr_type')
  language = match.group('language') or ''
  return field_name, _FIELD_TYPE_MAPPING[solr_type], language
