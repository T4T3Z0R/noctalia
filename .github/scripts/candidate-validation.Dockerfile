FROM docker.io/library/archlinux@sha256:375a401bb3c6376b37f40b6dfa25067db37ec78d0b7af67ae6315f43a0184a9d

RUN pacman -Syu --noconfirm --needed \
        base-devel git meson ninja pkgconf \
        wayland wayland-protocols \
        libglvnd freetype2 fontconfig noto-fonts \
        cairo pango harfbuzz \
        libxkbcommon glib2 \
        libsecret libsodium \
        sdbus-cpp libpipewire wireplumber polkit \
        pam curl libwebp libjxl libsndfile librsvg \
        libqalculate libxml2 \
        md4c tomlplusplus libical \
        nlohmann-json stb \
        jemalloc just clang clang22 \
    && pacman -Scc --noconfirm \
    && groupadd --gid 20000 builduser \
    && useradd --uid 20000 --gid 20000 \
        --create-home builduser \
    && install -d -m 700 \
        -o builduser -g builduser /source

ENV HOME=/home/builduser
ENV LANG=C.UTF-8

USER 20000:20000
WORKDIR /source

ENTRYPOINT ["/usr/bin/bash", "--noprofile", "--norc"]
