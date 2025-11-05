#!/bin/bash
#
# Copyright The Linux Foundation and each contributor to LFX.
# SPDX-License-Identifier: MIT

# This script requires jwt-client to be installed. See
# <https://github.com/mike-engel/jwt-cli> for instructions.

# Ensure we were passed at least 2 arguments.
if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <audience> <principal> [<email>]"
    exit 1
fi

aud=$1
principal=$2
email=$3

if [ -n "$email" ]; then
  payload="{\"email\": \"$email\"}"
else
  payload='{}'
fi

key_id="$(curl -s http://lfx-platform-heimdall.lfx.svc.cluster.local:4457/.well-known/jwks | jq -r '.keys.[0].kid')"

if [ -z "$key_id" ]; then
    echo "Failed to retrieve key ID from Heimdall"
    exit 1
fi

pem_temp_dir=$(mktemp -d)
kubectl get secret/heimdall-signer-cert -n lfx -o json | jq -r '.data["signer.pem"]' | base64 --decode > ${pem_temp_dir}/signer.pem

jwt encode \
    --alg PS256 \
    --kid $key_id \
    --exp=+300s \
    --nbf +0s \
    --jti "$(uuidgen)" \
    --payload "aud=$aud" \
    --payload "iss=heimdall" \
    --payload "sub=${principal#clients@}" \
    --payload "principal=$principal" \
    --secret "@${pem_temp_dir}/signer.pem" \
    "${payload}"

jwt_result=$?

rm -rf ${pem_temp_dir}

exit $jwt_result
