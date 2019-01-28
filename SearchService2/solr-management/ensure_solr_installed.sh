#!/usr/bin/env bash

set -e
set -u

VERSION=7.6.0

SOLR_EXTRACT_DIR=/opt/
SOLR_VAR_DIR=/var/solr/
SOLR_MANAGEMENT_DIR="$( realpath --strip "$( dirname "${BASH_SOURCE[0]}" )" )"


if ${SOLR_EXTRACT_DIR}/solr/bin/solr -version | grep "${VERSION}"
then
    echo "Solr ${VERSION} is already installed."
else
    echo "Downloading Solr ${VERSION} binaries."
    cd "${SOLR_EXTRACT_DIR}"
    wget "http://www-eu.apache.org/dist/lucene/solr/${VERSION}/solr-${VERSION}.tgz"
    tar xzf solr-${VERSION}.tgz solr-${VERSION}/bin/install_solr_service.sh --strip-components=2

    echo "Installing Solr ${VERSION}."
    # -n  Do not start solr service after install.
    # -f  Upgrade Solr. Overwrite symlink and init script of previous installation.
    sudo bash ./install_solr_service.sh solr-${VERSION}.tgz \
              -d "${SOLR_VAR_DIR}" \
              -i "${SOLR_EXTRACT_DIR}" \
              -n -f
fi

echo "Solr has been successfully installed."
