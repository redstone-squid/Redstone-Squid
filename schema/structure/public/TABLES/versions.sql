CREATE TABLE public.versions (
    id smallint NOT NULL,
    edition text NOT NULL,
    major_version smallint NOT NULL,
    minor_version smallint NOT NULL,
    patch_number smallint NOT NULL,
    data_version integer,
    CONSTRAINT versions_edition_check CHECK ((edition = ANY (ARRAY['Java'::text, 'Bedrock'::text]))),
    CONSTRAINT versions_number_check CHECK (((major_version >= 0) AND (minor_version >= 0) AND (patch_number >= 0)))
);

COMMENT ON TABLE public.versions IS 'A version of Minecraft that a build is compatible with.';

COMMENT ON COLUMN public.versions.data_version IS 'Minecraft''s world-format number, used when retargeting schematics.

None means no data version is recorded. Bedrock and early Java releases do not
have one.';

ALTER TABLE ONLY public.versions ALTER COLUMN id SET DEFAULT nextval('public.versions_id_seq'::regclass);

ALTER TABLE ONLY public.versions
    ADD CONSTRAINT versions_number_key UNIQUE (edition, major_version, minor_version, patch_number);

ALTER TABLE ONLY public.versions
    ADD CONSTRAINT versions_pkey PRIMARY KEY (id);

CREATE TRIGGER versions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();

