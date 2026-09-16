ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache openssh-keygen

COPY requirements.txt /tmp/requirements.txt
# bcrypt (needed by asyncssh to decrypt OpenSSH-format encrypted private keys)
# ships prebuilt wheels for amd64/aarch64 musllinux but not armv7 - add a Rust
# toolchain as a virtual package so pip can build it from source there, then
# drop the toolchain again so it doesn't stay in the final image.
RUN apk add --no-cache --virtual .build-deps rust cargo musl-dev libffi-dev \
 && pip3 install --no-cache-dir -r /tmp/requirements.txt \
 && apk del .build-deps

COPY rootfs /
COPY app /app

WORKDIR /app
