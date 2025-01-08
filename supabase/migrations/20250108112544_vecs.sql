create schema if not exists "vecs";

create table "vecs"."builds" (
    "id" character varying not null,
    "vec" vector(1536) not null,
    "metadata" jsonb not null default '{}'::jsonb
);


CREATE UNIQUE INDEX builds_pkey ON vecs.builds USING btree (id);

CREATE INDEX ix_vector_cosine_ops_hnsw_m16_efc64_5cae1b4 ON vecs.builds USING hnsw (vec vector_cosine_ops) WITH (m='16', ef_construction='64');

alter table "vecs"."builds" add constraint "builds_pkey" PRIMARY KEY using index "builds_pkey";