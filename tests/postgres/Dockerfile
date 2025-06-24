FROM postgres:17

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    postgresql-server-dev-17 \
    libsodium-dev \
    && rm -rf /var/lib/apt/lists/*

# Install pgsodium
RUN git clone https://github.com/michelp/pgsodium.git \
    && cd pgsodium \
    && make && make install

# Copy initialization scripts
COPY ./init.sql /docker-entrypoint-initdb.d/

# Set permissions for postgres user
RUN chown -R postgres:postgres /docker-entrypoint-initdb.d/ 