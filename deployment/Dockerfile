# Supply the verified, pinned image or digest used by the target deployment.
ARG PRETIX_BASE_IMAGE
FROM ${PRETIX_BASE_IMAGE}
USER root
COPY pretix_gocardless-*.whl /tmp/gocardless-wheel/
RUN python -m pip install --no-deps --force-reinstall /tmp/gocardless-wheel/*.whl
# Set this to the non-root runtime user verified in the original image.
ARG PRETIX_RUNTIME_USER
USER ${PRETIX_RUNTIME_USER}
