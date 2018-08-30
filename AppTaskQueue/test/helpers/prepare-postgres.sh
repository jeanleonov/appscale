#!/usr/bin/env bash
#
# Ensures that Postgres is installed on this machine.
# Creates test DB and user.
# Configures Postgres to accept host connections to new Database.


set -e
set -u


usage() {
    echo "Usage: ${0} --host <HOST> --dbname <DBNAME> --username <USERNAME> \\"
    echo "            --password <USER_PWD>"
    echo
    echo "Options:"
    echo "   --host <HOST>          Host IP to accept connections on"
    echo "   --dbname <DBNAME>      Database name to create"
    echo "   --username <USERNAME>  Role name to create"
    echo "   --password <USER_PWD>  Password to use for new user"
    exit 1
}

HOST=
DBNAME=
USERNAME=
PASSWORD=

# Let's get the command line arguments.
while [ $# -gt 0 ]; do
    if [ "${1}" = "--host" ]; then
        shift
        if [ -z "${1}" ]; then
            usage
        fi
        HOST="${1}"
        shift
        continue
    fi
    if [ "${1}" = "--dbname" ]; then
        shift
        if [ -z "${1}" ]; then
            usage
        fi
        DBNAME="${1}"
        shift
        continue
    fi
    if [ "${1}" = "--username" ]; then
        shift
        if [ -z "${1}" ]; then
            usage
        fi
        USERNAME="${1}"
        shift
        continue
    fi
    if [ "${1}" = "--password" ]; then
        shift
        if [ -z "${1}" ]; then
            usage
        fi
        PASSWORD="${1}"
        shift
        continue
    fi
    usage
done

if [ -z "${HOST}" ] || [ -z "${DBNAME}" ] || [ -z "${USERNAME}" ] || [ -z "${PASSWORD}" ]; then
    usage
fi


log() {
    local LEVEL=${2:-INFO}
    echo "$(date +'%Y-%m-%d %T'): $LEVEL $1"
}


log "Installing Postgres"
attempt=1
while ! (yes | apt-get install postgresql)
do
    if (( attempt > 15 )); then
        log "Failed to install postgresql after ${attempt} attempts" "ERROR"
        exit 1
    fi
    log "Failed to install postgresql. Retrying." "WARNING"
    ((attempt++))
    sleep ${attempt}
done

log "Checking if DB and user already exist"
if psql --dbname ${DBNAME} --username ${USERNAME} --host ${HOST} \
        --command 'SELECT current_timestamp;'
then
    log "DB and user are already configured"
    exit 0
fi

log "Creating Database and Role"
CREATE_ROLE="CREATE ROLE \"${USERNAME}\" WITH LOGIN PASSWORD '${PASSWORD}';"
sudo -u postgres psql --command "${CREATE_ROLE}"
sudo -u postgres createdb --owner "${USERNAME}" "${DBNAME}"
echo "${HOST}:5432:${DBNAME}:${USERNAME}:${PASSWORD}" > ~/.pgpass
chmod 600 ~/.pgpass
cp ~/.pgpass /root/.pgpass


log "Updating Postgres configs to accept host connections to the Database"
PG_VERSION=$(psql --version | awk '{ print $3 }' | awk -F '.' '{ print $1 "." $2 }')
PG_CONF="/etc/postgresql/${PG_VERSION}/main/postgresql.conf"
PG_HBA="/etc/postgresql/${PG_VERSION}/main/pg_hba.conf"

# Configure postgres to listen on the specified host
if grep -q -E "^listen_addresses *=" "${PG_CONF}"
then
    sed -i "s/^listen_addresses *=.*/listen_addresses = 'localhost,${HOST}'/" "${PG_CONF}"
else
    echo "listen_addresses = 'localhost,${HOST}'" >> "${PG_CONF}"
fi
cat >> "${PG_CONF}" << PERFORMANCE_CONFIGS

# DB Version: 10
# OS Type: linux
# DB Type: web
# Total Memory (RAM): 16 GB
# CPUs num: 8
# Connections num: 300
# Data Storage: hdd
#max_connections = 500
#shared_buffers = 3GB
effective_cache_size = 10GB
maintenance_work_mem = 1GB
checkpoint_completion_target = 0.7
wal_buffers = 16MB
default_statistics_target = 100
random_page_cost = 4
effective_io_concurrency = 2
work_mem = 3495kB
min_wal_size = 1GB
max_wal_size = 2GB
max_worker_processes = 8
max_parallel_workers_per_gather = 4
max_parallel_workers = 8
PERFORMANCE_CONFIGS

sed -i "s/^max_connections *=.*/max_connections = 500/" "${PG_CONF}"
sed -i "s/^shared_buffers *=.*/shared_buffers = 3GB/" "${PG_CONF}"


# Allow host connections to the specified DB
if grep -q -E "^host[ \t]+${DBNAME}[ \t]+${USERNAME}[ \t]+" "${PG_HBA}"
then
    sed -i "s|^host[ \t]+${DBNAME}[ \t]+${USERNAME}[ \t]+.*|host ${DBNAME} ${USERNAME} ${HOST}/0 md5|" "${PG_HBA}"
else
    echo "host ${DBNAME} ${USERNAME} ${HOST}/0 md5" >> "${PG_HBA}"
fi

log "Restarting Postgres"
service postgresql restart
