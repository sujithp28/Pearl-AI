# Mount point

`docker-compose.yml` bind-mounts this directory to `/workspace` inside the
container — it is the project Pearl operates on.

It is committed (rather than left to Docker to create) because Docker
creates a missing bind source owned by root, and the container runs as an
unprivileged user, so the first `up` would fail on permissions.

Point the mount at your own repository instead, or clone one in here.
