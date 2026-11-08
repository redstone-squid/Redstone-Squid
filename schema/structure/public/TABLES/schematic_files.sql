CREATE TABLE public.schematic_files (
    sha256 text NOT NULL,
    byte_size integer NOT NULL,
    source_format text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    object_key text NOT NULL,
    CONSTRAINT schematic_files_object_key_check CHECK ((object_key = ((('schematics/'::text || "left"(sha256, 2)) || '/'::text) || sha256))),
    CONSTRAINT schematic_files_size_bounded CHECK (((byte_size > 0) AND (byte_size <= 16777216)))
);

COMMENT ON TABLE public.schematic_files IS 'Relational metadata for a content-addressed schematic artifact.';

COMMENT ON COLUMN public.schematic_files.sha256 IS 'Lowercase hex SHA-256 of the object payload, and the identity of this row.';

COMMENT ON COLUMN public.schematic_files.byte_size IS 'Object payload size used to bound downloads.';

COMMENT ON COLUMN public.schematic_files.source_format IS 'The format the content sniffer identified, e.g. `litematic`.';

ALTER TABLE ONLY public.schematic_files
    ADD CONSTRAINT schematic_files_pkey PRIMARY KEY (sha256);

