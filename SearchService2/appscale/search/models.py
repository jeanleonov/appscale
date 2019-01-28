"""
Declares models to use in SearchService2. Instances of these classes
are just frozen containers of information.

It helps to remove protobuf-specific code from solr_adapter
and solr-specific code from api_methods. So both modules can talk on
unified language which corresponds to Google Search API documentation.
It doesn't cover all Google Search API objects, models are not defined
for objects like SearchParams and SortExpression, its properties are
either not supported or are passed as a keyword arguments of
SolrAdapter methods.

There are two types of models:
  - Models corresponding to Google Search API objects;
  - Helper models for solr_adapter.

Google documentations can be helpful
if you have any questions regarding Search API objects:
  https://cloud.google.com/appengine/docs/standard/python/search/documentclass
"""
import attr


# ==================================================
#     MODELS CORRESPONDING TO Search API OBJECTS
# --------------------------------------------------

@attr.s(cmp=False, hash=False, slots=True, frozen=True)
class Document(object):
  doc_id = attr.ib()
  fields = attr.ib()
  facets = attr.ib()
  language = attr.ib()
  rank = attr.ib()


@attr.s(cmp=False, hash=False, slots=True, frozen=True)
class Field(object):
  class Type(object):
    TEXT = "text"
    HTML = "html"
    ATOM = "atom"
    NUMBER = "number"
    DATE = "date"      # value is a datetime
    GEO = "geo"        # value is a tuple(lat, lng)

  type = attr.ib()
  name = attr.ib()
  value = attr.ib()
  language = attr.ib()


@attr.s(cmp=False, hash=False, slots=True, frozen=True)
class Facet(object):
  class Type(object):
    ATOM = "atom"
    NUMBER = "number"

  type = attr.ib()
  name = attr.ib()
  value = attr.ib()


@attr.s(cmp=False, hash=False, slots=True, frozen=True)
class ScoredDocument(object):
  doc_id = attr.ib()
  fields = attr.ib()
  language = attr.ib()
  sort_scores = attr.ib()
  expressions = attr.ib()
  cursor = attr.ib()
  rank = attr.ib()


@attr.s(cmp=False, hash=False, slots=True, frozen=True)
class SearchResult(object):
  num_found = attr.ib()
  scored_documents = attr.ib()
  cursor = attr.ib()
  facets = attr.ib()


# ======================================
#     HELPER MODELS FOR SOLR ADAPTER
# --------------------------------------

@attr.s(cmp=False, hash=False, slots=True, frozen=True)
class SolrIndexSchemaInfo(object):
  app_id = attr.ib()
  namespace = attr.ib()
  gae_index_name = attr.ib()
  collection = attr.ib()
  docs_number = attr.ib()
  heap_usage = attr.ib()
  size_in_bytes = attr.ib()
  fields = attr.ib()
  grouped_fields = attr.ib()


@attr.s(cmp=False, hash=False, slots=True, frozen=True)
class SolrSchemaFieldInfo(object):
  solr_name = attr.ib()
  gae_name = attr.ib()
  type = attr.ib()
  language = attr.ib()
  docs_number = attr.ib()


@attr.s(cmp=False, hash=False, slots=True, frozen=True)
class SolrQueryOptions(object):
  query_string = attr.ib()
  query_fields = attr.ib()
  def_type = attr.ib()


@attr.s(cmp=False, hash=False, slots=True, frozen=True)
class SolrSearchResult(object):
  num_found = attr.ib()
  documents = attr.ib()
  cursor = attr.ib()
  facets = attr.ib()
