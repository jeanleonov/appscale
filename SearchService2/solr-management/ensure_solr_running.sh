#!/usr/bin/env bash

set -e
set -u

SOLR_EXTRACT_DIR=/opt/
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
solr_zk="${SOLR_EXTRACT_DIR}/solr/bin/solr zk"

if ${solr_zk} ls ${SOLR_ZK_ROOT} -z "${FIRST_ZK}"
then
    echo "Zookeeper root is already created."
else
    echo "Creating zookeeper root."
    ${solr_zk} mkroot ${SOLR_ZK_ROOT} -z "${FIRST_ZK}" \
      || ${solr_zk} ls ${SOLR_ZK_ROOT} -z "${FIRST_ZK}"
    # We shouldn't fail if root was created after we entered to else clause.
fi


# Generating proper solr.in.sh with needed SolrCloud configurations.
HEAP_REDUCTION="${HEAP_REDUCTION:-0.0}"
TOTAL_MEM_KB=$(awk '/MemTotal/ { print $2 }' /proc/meminfo)
# Give Solr at most half of total memory minus heap reduction.
SOLR_MEM_MB=$(echo "$TOTAL_MEM_KB $HEAP_REDUCTION" \
              | awk '{ printf "%d", $1 * (1 - $2) / 1024 / 2 }')

echo "Ensuring ulimit properties are set for Solr"
grep -q "solr \+hard \+nofile \+65535" /etc/security/limits.conf \
  || echo "solr hard nofile 65535" >> /etc/security/limits.conf
grep -q "solr \+soft \+nofile \+65535" /etc/security/limits.conf \
  || echo "solr soft nofile 65535" >> /etc/security/limits.conf
grep -q "solr \+hard \+nproc \+65535" /etc/security/limits.conf \
  || echo "solr hard nproc 65535" >> /etc/security/limits.conf
grep -q "solr \+soft \+nproc \+65535" /etc/security/limits.conf \
  || echo "solr soft nproc 65535" >> /etc/security/limits.conf

export SOLR_MEM="${SOLR_MEM_MB}m"
export ZK_HOST
export PRIVATE_IP
envsubst < "${SOLR_MANAGEMENT_DIR}/solr.in.sh" > "/tmp/solr.in.sh"
if cmp -s "/tmp/solr.in.sh" "/etc/default/solr.in.sh"
then
    echo "/etc/default/solr.in.sh has no changes."
    echo "Making sure Solr is running."
    sudo systemctl start solr
    sudo systemctl enable solr
else
    echo "Copying new solr.in.sh to /etc/default/solr.in.sh"
    sudo cp "/tmp/solr.in.sh" "/etc/default/solr.in.sh"
    echo "Making sure Solr is restarted."
    sudo systemctl restart solr
    sudo systemctl enable solr
fi

echo "Making sure appscale-specific config set is uploaded to zookeeper."
"${SOLR_MANAGEMENT_DIR}"/ensure_config_set.sh

echo "Solr is installed, configured and started."
