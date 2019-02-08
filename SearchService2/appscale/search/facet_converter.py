import collections
import logging
from distutils.errors import UnknownFileError

from appscale.search.constants import InvalidRequest
from appscale.search.models import FacetResult

logger = logging.getLogger(__name__)


def discover_facets(atom_facets_stats, facets_count, value_limit):
  """ Prepares a list of facets to request from Solr based on
  facets statistics.

  Args:
    atom_facets_stats: a list of tuples (<SolrSchemaFieldInfo>, <count>).
    facets_count: an int - number of top facets to discover.
    value_limit: an int - max number of values to request.
  Returns:
    A list of tuples (<facet key>, <facet info>).
  """
  sorted_facets = sorted(atom_facets_stats, key=lambda item: -item[1])
  top_facets = sorted_facets[:facets_count]
  facet_items = []
  for solr_field, documents_count in top_facets:
    facet_key = '{}*'.format(solr_field.gae_name)
    facet_info = {
      'type': 'terms',
      'field': solr_field.solr_name,
      'limit': value_limit
    }
    facet_items.append((facet_key, facet_info))
  return facet_items


def generate_refinement_filter(schema_grouped_facets, refinements):
  """ Prepare Solr filter string according to refinements list.

  Args:
    schema_grouped_facets: a dict - maps GAE facet name to list of solr fields.
    refinements: a list of FacetRefinement.
  Returns:
    A str representing Solr filter query.
  """
  grouped_refinements = collections.defaultdict(list)
  for refinement in refinements:
    grouped_refinements[refinement.name].append(refinement)

  and_elements = []
  for facet_name, refinements_group in grouped_refinements.items():
    facet_field = _get_facet_field(schema_grouped_facets, facet_name)
    solr_name = facet_field.solr_name
    or_elements = []
    for refinement in refinements_group:
      if refinement.value:
        or_elements.append('{}:"{}"'.format(solr_name, refinement.value))
      else:
        start, end = refinement.range
        start = start if start is not None else '*'
        end = end if end is not None else '*'
        or_elements.append('{}:[{} TO {})'.format(solr_name, start, end))

    and_elements.append(
      '({})'.format(' OR '.join(element for element in or_elements))
    )

  return ' AND '.join(and_elements)


def convert_facet_requests(schema_grouped_facets, facet_requests):
  """ Prepares a list of facets to request from Solr based on
  user specified facet requests.

  Args:
    schema_grouped_facets: a dict - maps GAE facet name to list of solr fields.
    facet_requests: a list of FacetRequest.
  Returns:
    A list of tuples (<facet key>, <facet info>).
  """
  facet_items = []
  for facet_request in facet_requests:
    facet_field = _get_facet_field(schema_grouped_facets, facet_request.name)
    solr_name = facet_field.solr_name
    if facet_request.values:
      for value in facet_request.values:
        facet_key = '{}:{}'.format(facet_request.name, value)
        facet_info = {'query': '{}:"{}"'.format(solr_name, value)}
        facet_items.append((facet_key, facet_info))
    elif facet_request.ranges:
      for start, end in facet_request.ranges:
        range_str = '[{} TO {})'.format(start if start is not None else '*',
                                        end if end is not None else '*')
        facet_key = '{}#{}'.format(facet_request.name, range_str)
        facet_info = {'query': '{}:{}'.format(solr_name, range_str)}
        facet_items.append((facet_key, facet_info))
    else:
      facet_key = '{}*'.format(facet_request.name)
      facet_info = {
        'type': 'terms',
        'field': solr_name,
        'limit': facet_request.value_limit
      }
      facet_items.append((facet_key, facet_info))
  return facet_items


def convert_facet_results(solr_facet_results):
  """ Converts raw Solr results to a list of FacetResult.

  Args:
    solr_facet_results: A dict containing facets from Solr response.
  Returns:
    A list of FacetResult.
  """
  logger.info('Solr Facet Results: {}'.format(solr_facet_results))
  facet_values = collections.defaultdict(list)
  facet_ranges = collections.defaultdict(list)
  facet_results = []
  for facet_key, solr_facet_result in solr_facet_results.items():
    if ':' in facet_key:
      gae_facet_name, value = facet_key.split(':')
      value_tuple = (value, solr_facet_result['count'])
      facet_values[gae_facet_name].append(value_tuple)
    elif '#' in facet_key:
      gae_facet_name, range_str = facet_key.split('#')
      start_str, end_str = range_str.strip('[)').split(' TO ')
      range_tuple = (
        int(start_str) if start_str != '*' else None,
        int(end_str) if end_str != '*' else None,
        solr_facet_result['count']
      )
      facet_ranges[gae_facet_name].append(range_tuple)
    elif '*' in facet_key:
      gae_facet_name = facet_key.strip('*')
      buckets = solr_facet_result['buckets']
      values = [(bucket['val'], bucket['count']) for bucket in buckets]
      facet_result = FacetResult(name=gae_facet_name, values=values, ranges=[])
      facet_results.append(facet_result)

  facet_results += [
    FacetResult(name=facet_name, values=values, ranges=[])
    for facet_name, values in facet_values.items()
  ]
  facet_results += [
    FacetResult(name=facet_name, values=[], ranges=ranges)
    for facet_name, ranges in facet_ranges.items()
  ]
  return facet_results


def _get_facet_field(schema_grouped_facets, gae_facet_name):
  """ A helper function which retrieves solr field corresponding to
  GAE facet with specified name.
  The only real feature of this function is to report warning
  if there are multiple facets (with different type) has the same name.

  Args:
    schema_grouped_facets: a dict - maps GAE facet name to list of solr fields.
    gae_facet_name: a str representing GAE facet name.
  Returns:
    an instance of SolrSchemaFieldInfo.
  """
  try:
    facets_group = schema_grouped_facets[gae_facet_name]
  except KeyError:
    raise InvalidRequest('Unknown facet "{}"'.format(gae_facet_name))
  if len(facets_group) > 1:
    # Multiple facet types are used for facet with the same GAE name,
    # so let's pick most "popular" facet of those.
    facet_types = ', '.join(
      '{}: {} docs'.format(facet.type, facet.docs_number)
      for facet in facets_group
    )
    logger.warning(
      'Multiple facet types are used for facet {} ({}).'
      'Trying to compute facet for {}.'
      .format(gae_facet_name, facet_types, facets_group[0].type)
    )
  return facets_group[0]
