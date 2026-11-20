FROM node:24-bookworm-slim AS node
FROM bookadaptor-app:latest
COPY --from=node /usr/local/bin/node /usr/local/bin/node
