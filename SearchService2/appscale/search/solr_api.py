"""
Helper module for handling routine work when communicating with Solr API.
It knows where Solr servers are located, how to pass request arguments
to API methods, etc.
"""
import itertools
import json
import logging
import socket

from appscale.search.models import SolrSearchResult

from urllib.parse import urlencode

from tornado import httpclient, gen, ioloop
from appscale.common import appscale_info

from appscale.search.constants import (
  SolrIsNotReachable, SOLR_TIMEOUT, SolrClientError, SolrServerError,
  SolrError, SOLR_COMMIT_WITHIN, APPSCALE_CONFIG_SET_NAME
)

logger = logging.getLogger(__name__)


def tornado_synchronous(coroutine):
  """ Builds synchronous function based on tornado coroutine.

  Args:
    coroutine: a generator (tornado.gen.coroutine).
  Returns:
    A regular python function.
  """
  def synchronous_coroutine(*args, **kwargs):
    async = lambda: coroutine(*args, **kwargs)
    # Like synchronous HTTPClient, create separate IOLoop for sync code
    io_loop = ioloop.IOLoop(make_current=False)
    try:
      return io_loop.run_sync(async)
    finally:
      io_loop.close()
  return synchronous_coroutine


class SolrAPI(object):
  """
  A helper class for performing basic operations with Solr.
  """

  def __init__(self, zk_client, solr_zk_root):
    """ Initializes SolrAPI object.
    Configures zookeeper watching of Solr live nodes.

    Args:
      zk_client:
      solr_zk_root:
    """
    self._zk_client = zk_client
    self._solr_zk_root = solr_zk_root
    self._solr_live_nodes_list = []
    self._solr_live_nodes_cycle = itertools.cycle(self._solr_live_nodes_list)
    self._local_solr = None
    self._private_ip = appscale_info.get_private_ip()
    self._zk_client.ChildrenWatch(
      '{}/live_nodes'.format(self._solr_zk_root), self._update_live_nodes
    )
    list_collections_sync = tornado_synchronous(self.list_collections)
    self._collections_cache = list_collections_sync()

  def _update_live_nodes(self, new_live_nodes):
    """ Updates information about Solr live nodes.

    Args:
      new_live_nodes: a list of strings representing Solr location.
    """
    self._solr_live_nodes_list = [
      node.replace('_solr', '') for node in new_live_nodes
    ]
    self._solr_live_nodes_cycle = itertools.cycle(self._solr_live_nodes_list)
    self._local_solr = next(
      (node for node in self._solr_live_nodes_list
       if node.startswith(self._private_ip)), None
    )
    logger.info('Got a new list of solr live nodes: {}'
                .format(self._solr_live_nodes_list))

  @property
  def solr_location(self):
    """
    Returns:
      A string representing Solr location (preferably local).
    """
    if self._local_solr:
      return self._local_solr
    if not self._solr_live_nodes_list:
      raise SolrIsNotReachable('There are no Solr live nodes')
    return next(self._solr_live_nodes_cycle)

  @gen.coroutine
  def request(self, method, path, params=None, json_data=None, headers=None):
    """ Sends HTTP request to one of Solr live nodes.

    Args:
      method: a str - HTTP method.
      path: a str - HTTP path.
      params: a dict containing URL params
      json_data: a json-serializable object to pass in request body.
      headers: a dictionary containing HTTP headers to send.
    Returns (asynchronously):
      A httpclient.HTTPResponse.
    """
    if params:
      url_params = urlencode(params)
      url = 'http://{}{}?{}'.format(self.solr_location, path, url_params)
    else:
      url = 'http://{}{}'.format(self.solr_location, path)

    if json_data is not None:
      headers = headers or {}
      headers['Content-type'] = 'application/json'
      body = json.dumps(json_data)
    else:
      body = None

    async_http_client = httpclient.AsyncHTTPClient()
    request = httpclient.HTTPRequest(
      url=url, method=method, headers=headers, body=body,
      connect_timeout=SOLR_TIMEOUT, request_timeout=SOLR_TIMEOUT,
      allow_nonstandard_methods=True
    )
    try:
      response = yield async_http_client.fetch(request)
    except socket.error as err:
      raise SolrIsNotReachable('Socket error ({})'.format(err))
    except httpclient.HTTPError as err:
      msg = u"Error during Solr call {url} ({err})".format(url=url, err=err)
      if err.response.body.decode('utf-8'):
        try:
          err_details = json.loads(err.response.body.decode('utf-8'))['error']['msg']
        except ValueError:
          err_details = err.response.body.decode('utf-8')
        msg += u"\nError details: {}".format(err_details)
      logger.error(msg)
      if err.response.code < 500:
        raise SolrClientError(msg)
      else:
        raise SolrServerError(msg)

    raise gen.Return(response)

  @gen.coroutine
  def get(self, path, params=None, json_data=None, headers=None):
    """ GET wrapper of request method """
    response = yield self.request('GET', path, params, json_data, headers)
    raise gen.Return(response)

  @gen.coroutine
  def post(self, path, params=None, json_data=None, headers=None):
    """ POST wrapper of request method """
    response = yield self.request('POST', path, params, json_data, headers)
    raise gen.Return(response)

  @gen.coroutine
  def list_collections(self):
    """ Lists names of collections created in Solr.

    Returns (asynchronously):
      A list of collection names present in Solr.
    """
    try:
      response = yield self.get('/v2/collections')
      raise gen.Return(json.loads(response.body.decode('utf-8'))['collections'])
    except SolrError:
      logger.exception('Failed to list collections')
      raise

  @gen.coroutine
  def ensure_collection(self, collection):
    """ Asynchronously ensures that Solr collection is created.

    Args:
      collection: a str - name of collection to make sure is created.
    """
    # Check if collection is already known locally
    if collection in self._collections_cache:
      return
    # Update local cache and check again
    self._collections_cache = yield self.list_collections()
    if collection in self._collections_cache:
      return
    # Create Solr collection
    try:
      # Collection creation in API v2 doesn't support collection.configName yet.
      # So using old API (/solr/...).
      response = yield self.post(
        '/solr/admin/collections',
        params={
          'action': 'CREATE',
          'name': collection,
          'collection.configName': APPSCALE_CONFIG_SET_NAME,
          'numShards': 1,            # TODO: Store per-project replications
          'replicationFactor': 1,    # ||||  and sharding configs in Zookeeper.
          'maxShardsPerNode': 1,     #
        }
      )
      logger.info('Successfully created collection {} ({})'
                  .format(collection, response.body))
      self._collections_cache = yield self.list_collections()
    except SolrError as err:
      if 'collection already exists' in err.error_detail:
        logger.warning('Collection {} already exists'.format(collection))
      else:
        logger.warning('Failed to create collection {}'.format(collection))
        raise

  @gen.coroutine
  def get_schema_info(self, collection):
    """ Retrieves collection shema information. It uses Luke handler
    because, in contrast to a regular get method of Schema API,
    Luke handler provides information about dynamically created fields.

    Args:
      collection:
    Returns (asynchronously):
      A dict containing information about Solr collection.
    """
    yield self.ensure_collection(collection)
    try:
      # Luke handler is not supported in API v2 yet.
      # /v2/collections/<COLLECTION>/schema/fields doesn't show dynamically
      # created fields.
      # So using old API (/solr/...).
      response = yield self.get(
        '/solr/{}/admin/luke?numTerms=0'.format(collection)
      )
      raise gen.Return(json.loads(response.body.decode('utf-8')))
    except SolrError:
      logger.warning('Failed to fetch fields list for collection {}'
                     .format(collection))
      raise

  @gen.coroutine
  def put_documents(self, collection, documents):
    """ Asynchronously puts documents into Solr collection.

    Args:
      collection: a str - name of Solr collection.
      documents: a list of documents to put.
    """
    yield self.ensure_collection(collection)
    try:
      yield self.post(
        '/v2/collections/{}/update'.format(collection),
        params={'commitWithin': SOLR_COMMIT_WITHIN},
        json_data=documents
      )
      logger.info('Successfully indexed {} documents to collection {}'
                  .format(len(documents), collection))
    except SolrError:
      logger.warning('Failed to put {} documents to collection {}'
                     .format(len(documents), collection))
      raise

  @gen.coroutine
  def delete_documents(self, collection, ids):
    """ Asynchronously deletes documents from Solr collection.

    Args:
      collection: a str - name of Solr collection.
      ids: a list of document IDs to delete.
    """
    yield self.ensure_collection(collection)
    try:
      # Delete operation doesn't work with API v2 yet.
      # So using old API (/solr/...).
      yield self.post(
        '/solr/{}/update'.format(collection),
        params={'commitWithin': SOLR_COMMIT_WITHIN},
        json_data={"delete": ids}
      )
      logger.info('Successfully deleted {} documents from collection {}'
                  .format(len(ids), collection))
    except SolrError:
      logger.warning('Failed to delete {} documents from collection {}'
                     .format(len(ids), collection))
      raise

  @gen.coroutine
  def query_documents(self, collection, query, filter_=None, offset=None,
                      limit=None, fields=None, sort=None, facet=None,
                      cursor=None, def_type=None, query_fields=None):
    """ Queries Solr for documents matching specified query.

    Args:
      collection: a str - name of Solr collection
      query: a str - Solr query string.
      filter_: a str - Solr filter criteria.
      offset: a int - number of first document to skip.
      limit: a int - max number of document to return.
      fields: a list of field names to return for each document.
      sort: a list of field names suffixed with direction to order results by.
      facet: a dict describing facets to compute.
      cursor: a str - query cursors.
      def_type: a str - query parser type to use.
      query_fields: a list of field names to run query against.
    Returns (asynchronously):
      A SolrSearchResult containing documents, facets, cursor
      and total number of documents matching the query.
    """
    yield self.ensure_collection(collection)

    # Query params which are not supported by JSON Request API yet
    # should go inside "params" attribute.
    # See https://lucene.apache.org/solr/guide/7_6/json-request-api.html
    # for more details.
    params = {
      key: value for key, value in [
        ('cursorMark', cursor),
        ('defType', def_type),
        ('qf', ' '.join(query_fields) if query_fields else ''),
      ]
      if value is not None
    }
    json_data = {
      key: value for key, value in [
        ('query', query),
        ('filter', filter_),
        ('offset', offset),
        ('limit', limit),
        ('fields', fields),
        ('sort', ','.join(sort) if sort else ''),
        ('facet', facet),
        ('params', params)
      ]
      if value is not None
    }

    logger.debug(u'QUERY_BODY: {}'.format(json.dumps(json_data)))

    try:
      response = yield self.post(
        '/v2/collections/{}/query'.format(collection),
        json_data=json_data
      )
      json_response = json.loads(response.body.decode('utf-8'))
      query_response = json_response['response']
      solr_search_result = SolrSearchResult(
        num_found=query_response['numFound'],
        documents=query_response['docs'],
        cursor=json_response.get('nextCursorMark'),
        facets=json_response.get('facets')
      )
      logger.info('Found {} and fetched {} documents from collection {}'
                  .format(solr_search_result.num_found,
                          len(solr_search_result.documents), collection))
      raise gen.Return(solr_search_result)
    except SolrError:
      logger.warning('Failed to execute query {} against collection {}'
                     .format(json_data, collection))
      raise
