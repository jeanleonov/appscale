"""
The main modules of SearchService2.
It starts tornado http server which handles HTTP request
which contains Remote API protocol buffer request
which wraps Search API protocol buffer request.

This module reads Remote API request, and routes containing Search API
request to proper method of APIMethods.
"""
import argparse
import logging

from kazoo.client import KazooClient

from appscale.common import appscale_info
from appscale.common.constants import LOG_FORMAT, ZK_PERSISTENT_RECONNECTS
from tornado import ioloop, web, gen

from appscale.search import api_methods
from appscale.search.constants import SearchServiceError
from appscale.search.protocols import search_pb2, remote_api_pb2

logger = logging.getLogger(__name__)


class ProtobufferAPIHandler(web.RequestHandler):
  """ Serves Protobuffer requests to SearchService2. """

  def initialize(self, api):
    self.api = api

  @gen.coroutine
  def post(self):
    """ Function which handles POST requests. Data of the request is the
    request from the AppServer in an encoded protocol buffer format. """
    http_request_data = self.request.body
    pb_type = self.request.headers['protocolbuffertype']
    if pb_type != 'Request':
      raise NotImplementedError('Unknown protocolbuffertype {}'.format(pb_type))

    # Get app_id from appdata
    app_id = self.request.headers['appdata'].split(':')[0]

    remote_api_request = remote_api_pb2.Request()
    remote_api_request.ParseFromString(http_request_data)
    remote_api_response = remote_api_pb2.Response()
    try:
      # Make sure remote_api_request has search api method specified
      if not remote_api_request.HasField('method'):
        raise SearchServiceError(search_pb2.SearchServiceError.INVALID_REQUEST,
                                 'Method was not set in request')
      # Make sure remote_api_request has search api request specified
      if not remote_api_request.HasField('request'):
        raise SearchServiceError(search_pb2.SearchServiceError.INVALID_REQUEST,
                                 'Request data is not set in request')

      # Handle Search API request of specific
      search_api_method = remote_api_request.method
      search_api_request_data = remote_api_request.request
      logger.debug('Handling SearchAPI.{} request..'.format(search_api_method))
      search_api_response = yield self.handle_search_api_request(
        app_id, search_api_method, search_api_request_data
      )

      # Set encoded Search API response to Remote API response
      remote_api_response.response = search_api_response.SerializeToString()

    except SearchServiceError as err:
      # Set error information to Remote API response
      service_error_pb = remote_api_response.application_error
      service_error_pb.code = err.error_code
      service_error_pb.detail = err.error_detail
      if err.search_api_response:
        # Write also whatever Search API response is provided
        encoded_response = err.search_api_response.SerializeToString()
        remote_api_response.response = encoded_response

      # Write error details to log
      if err.error_code == search_pb2.SearchServiceError.INTERNAL_ERROR:
        logger.exception('InternalError: {}'.format(err.error_detail))
      else:
        logger.warning('SearchServiceError "{}" ({})'
                       .format(err.error_name, err.error_detail))

    # Write encoded Remote API response
    self.write(remote_api_response.SerializeToString())

  @gen.coroutine
  def handle_search_api_request(self, app_id, search_api_method,
                                search_api_req_data):
    """ Handles Search API request.

    Args:
      app_id: A string representing project_id.
      search_api_method: A string representing name of Search API method.
      search_api_req_data: Encoded protobuffer Search API request.
    Returns:
      An instance of protobuffer Search API response.
    """
    try:
      req_class, resp_class, executor = self.api[search_api_method]
    except KeyError:
      raise SearchServiceError(
        search_pb2.SearchServiceError.INVALID_REQUEST,
        'Unknown request method "{}"'.format(search_api_method)
      )
    search_api_req = req_class()
    search_api_req.ParseFromString(search_api_req_data)
    if not search_api_req.app_id:
      search_api_req.app_id = app_id
    search_api_resp = resp_class()
    yield executor(search_api_req, search_api_resp)
    raise gen.Return(search_api_resp)


def prepare_api_methods(zk_client):
  """ Instantiates APIMethods and defines API methods routing.

  Args:
    zk_client: an instance of kazoo.client.KazooClient.
  Returns:
    a dict which maps Search API method name to a tuple of
    (<request_class>, <response_class>, <coroutine_to_handle_request>)
  """
  api = api_methods.APIMethods(zk_client)
  return {
    'IndexDocument': (
      search_pb2.IndexDocumentRequest,
      search_pb2.IndexDocumentResponse,
      api.index_document
    ),
    'DeleteDocument': (
      search_pb2.DeleteDocumentRequest,
      search_pb2.DeleteDocumentResponse,
      api.delete_document
    ),
    'ListIndexes': (
      search_pb2.ListIndexesRequest,
      search_pb2.ListIndexesResponse,
      api.list_indexes
    ),
    'ListDocuments': (
      search_pb2.ListDocumentsRequest,
      search_pb2.ListDocumentsResponse,
      api.list_documents
    ),
    'Search': (
      search_pb2.SearchRequest,
      search_pb2.SearchResponse,
      api.search
    )
  }


def main():
  """ Start SearchService2 server. """
  logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)

  parser = argparse.ArgumentParser()
  parser.add_argument(
    '-v', '--verbose', action='store_true',
    help='Output debug-level logging')
  parser.add_argument(
    '-p', '--port', type=int, help='The port to listen on')
  parser.add_argument(
    '-z', '--zk-location', default=None,
    help='Comma-separated list of ZooKeeper locations')
  args = parser.parse_args()

  if args.verbose:
    logging.getLogger('appscale').setLevel(logging.DEBUG)

  if args.zk_location is not None:
    zk_locations = args.zk_location.split(',')
  else:
    zk_locations = appscale_info.get_zk_node_ips()
  zk_client = KazooClient(
    hosts=','.join(zk_locations),
    connection_retry=ZK_PERSISTENT_RECONNECTS
  )
  zk_client.start()
  api = prepare_api_methods(zk_client)

  logging.info('Starting server on port {}'.format(args.port))
  app = web.Application([
    (r'/?', ProtobufferAPIHandler, {'api': api}),
  ])
  app.listen(args.port)
  io_loop = ioloop.IOLoop.current()
  io_loop.start()
