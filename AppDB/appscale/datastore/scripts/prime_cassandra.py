""" Create Cassandra keyspace and initial tables. """

import argparse
import logging

from appscale.common.constants import LOG_FORMAT
from ..cassandra_env import schema


def main():
  logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)

  parser = argparse.ArgumentParser()
  group = parser.add_mutually_exclusive_group(required=True)
  group.add_argument('--replication', type=int,
                     help='The replication factor for the keyspace')
  group.add_argument('--check', action='store_true',
                     help='Check if the required tables are present')
  args = parser.parse_args()

  if args.check:
    assert schema.primed()
  else:
    schema.prime_cassandra(args.replication)
