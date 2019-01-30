#!/usr/bin/env bash

set -e
set -u

SOLR_EXTRACT_DIR=/opt/
SOLR_VAR_DIR=/var/solr/
SOLR_MANAGEMENT_DIR="$( realpath --strip "$( dirname "${BASH_SOURCE[0]}" )" )"

# Check if Solr is installed
VERSION=7.6.0
if ! ${SOLR_EXTRACT_DIR}/solr/bin/solr -version | grep "${VERSION}"
then
    echo "Can not start Solr ${VERSION} as it's not installed."
    exit 1
fi

# Root path for all SolrCloud nodes in Zookeeper.
SOLR_ZK_ROOT=/solr

# Determine zookeeper hosts
FIRST_ZK=$(head -1 /etc/appscale/zookeeper_locations)
ZK_HOST="${FIRST_ZK}"
for host in $(tail -n +2 /etc/appscale/zookeeper_locations)
do
    ZK_HOST="${ZK_HOST},${host}"
done
ZK_HOST="${ZK_HOST}${SOLR_ZK_ROOT}"
PRIVATE_IP=$(cat /etc/appscale/my_private_ip)


if ${SOLR_EXTRACT_DIR}/solr/bin/solr zk ls ${SOLR_ZK_ROOT} -z "${FIRST_ZK}"
then
    echo "Zookeeper root is already created."
else
    echo "Creating zookeeper root is created."
    ${SOLR_EXTRACT_DIR}/solr/bin/solr zk mkroot ${SOLR_ZK_ROOT} -z "${FIRST_ZK}"
fi

# Generating proper solr.in.sh with needed SolrCloud configurations.
export ZK_HOST
export PRIVATE_IP
export SOLR_MEM="${SOLR_MEM:-256m}"
envsubst < "${SOLR_MANAGEMENT_DIR}/solr.in.sh" > "/tmp/solr.in.sh"
if cmp -s "/tmp/solr.in.sh" "/etc/default/solr.in.sh"
then
    echo "/etc/default/solr.in.sh has no changes."
    echo "Making sure Solr is running."
    sudo service solr start
else
    echo "Copying new solr.in.sh to /etc/default/solr.in.sh"
    sudo cp "/tmp/solr.in.sh" "/etc/default/solr.in.sh"
    echo "Making sure Solr is restarted."
    sudo service solr restart
fi

echo "Making sure appscale-specific config set is uploaded to zookeeper."
"${SOLR_MANAGEMENT_DIR}"/ensure_config_set.sh

echo "Solr is installed, configured and started."
