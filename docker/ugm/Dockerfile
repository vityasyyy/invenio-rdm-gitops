# Dockerfile that builds a fully functional image of the UGM Invenio instance.
#
# This image installs all Python dependencies for the application. It's based
# on Almalinux (https://github.com/inveniosoftware/docker-invenio)
# and includes Pip, Pipenv, Node.js, NPM and some few standard libraries
# Invenio usually needs.
#
# Adapted from gunturbudi/invenio-ugm for Kubernetes:
#   - Build context is the repo root (not docker/ugm/) so instance-layer dirs
#     (site/, templates/, static/, assets/, app_data/, translations/) resolve.
#   - Pipfile/Pipfile.lock live in docker/ugm/ and are copied from there.
#   - uwsgi ini files are copied from docker/ugm/ (HTTP socket, uid 1654 non-root).
#
# Note: It is important to keep the commands in this file in sync with the
# bootstrap script located in ./scripts/bootstrap.

FROM registry.cern.ch/inveniosoftware/almalinux:1

COPY site ./site
COPY docker/ugm/Pipfile docker/ugm/Pipfile.lock ./
RUN pipenv install --deploy --system

COPY ./docker/ugm/uwsgi_ui.ini ./docker/ugm/uwsgi_rest.ini ${INVENIO_INSTANCE_PATH}
COPY ./docker/ugm/invenio.cfg ${INVENIO_INSTANCE_PATH}
COPY ./templates/ ${INVENIO_INSTANCE_PATH}/templates/
COPY ./app_data/ ${INVENIO_INSTANCE_PATH}/app_data/
COPY ./translations/ ${INVENIO_INSTANCE_PATH}/translations/
COPY ./ .

RUN cp -r ./static/. ${INVENIO_INSTANCE_PATH}/static/ && \
    cp -r ./assets/. ${INVENIO_INSTANCE_PATH}/assets/ && \
    invenio collect --verbose  && \
    invenio webpack buildall

# K8s hardening: run as a non-root user with the repo's pinned uid/gid 1654.
# (The security-context in the K8s manifests enforces runAsUser/runAsGroup/fsGroup.)
# The base image ships an 'invenio' user at uid 1000 (gid 0); we replace it with
# a uid/gid 1654 user so the runtime runAsUser:1654 can read/write the instance.
RUN userdel -f invenio 2>/dev/null; groupadd -f -g 1654 invenio; \
    useradd -r -u 1654 -g 1654 --home-dir=/opt/invenio --shell=/bin/bash invenio; \
    chown -R 1654:1654 ${INVENIO_INSTANCE_PATH}

USER invenio
EXPOSE 5000

# Default command. K8s manifests override args with uwsgi --ini, which works
# because this is exec-form CMD (no bash -c wrapper swallowing extra args).
CMD ["uwsgi", "--ini", "/opt/invenio/var/instance/uwsgi_ui.ini"]
