#!/bin/bash

#
# Copyright (C) 2025 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

# Terminate on error
set -e

# Prepare variables for later use
images=()
# The image will be pushed to GitHub container registry
repobase="${REPOBASE:-ghcr.io/nethserver}"
# Configure the image name
reponame="metrics"

# Create a new empty container image
container=$(buildah from scratch)

# Add imageroot directory to the container image
buildah add "${container}" imageroot /imageroot
buildah add "${container}" ui/ /ui
# Setup the entrypoint, ask to reserve one TCP port with the label and set a rootless container
buildah config --entrypoint=/ \
    --label="org.nethserver.authorizations=traefik@any:routeadm" \
    --label="org.nethserver.rootfull=0" \
    --label="org.nethserver.max-per-node=1" \
    --label='org.nethserver.flags=core_module no_data_backup' \
    --label="org.nethserver.images=quay.io/prometheus/prometheus:v3.5.1 quay.io/prometheus/alertmanager:v0.31.0 docker.io/grafana/grafana:12.2.4 ghcr.io/nethserver/alert-proxy:${IMAGETAG:-latest}" \
    "${container}"
# Commit the image
buildah commit "${container}" "${repobase}/${reponame}"

# Append the image URL to the images array
images+=("${repobase}/${reponame}")

# Build alert-proxy container
pushd alert-proxy
buildah build -t "${repobase}/alert-proxy" Containerfile
images+=("${repobase}/alert-proxy")
popd


#
# NOTICE:
#
# It is possible to build and publish multiple images.
#
# 1. create another buildah container
# 2. add things to it and commit it
# 3. append the image url to the images array
#

#
# Setup CI when pushing to Github.
# Warning! docker::// protocol expects lowercase letters (,,)
if [[ -n "${CI}" ]]; then
    # Set output value for Github Actions
    printf "::set-output name=images::%s\n" "${images[*],,}"
else
    # Just print info for manual push
    printf "Publish the images with:\n\n"
    for image in "${images[@],,}"; do printf "  buildah push %s docker://%s:%s\n" "${image}" "${image}" "${IMAGETAG:-latest}" ; done
    printf "\n"
fi
