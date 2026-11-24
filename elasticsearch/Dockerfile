ARG ELASTIC_VERSION

# https://www.docker.elastic.co/
FROM docker.elastic.co/elasticsearch/elasticsearch:${ELASTIC_VERSION:-9.5.6}

# Add your elasticsearch plugins setup here
# Example: RUN elasticsearch-plugin install analysis-icu

# DEMO ONLY: bake the JWT HMAC signing key into the keystore so the jwt1 realm
# (configured in elasticsearch.yml) can start. Passed from .env via compose.
ARG JWT_HMAC_KEY
RUN printf '%s' "$JWT_HMAC_KEY" | bin/elasticsearch-keystore create \
 && printf '%s' "$JWT_HMAC_KEY" | bin/elasticsearch-keystore add -x xpack.security.authc.realms.jwt.jwt1.hmac_key
