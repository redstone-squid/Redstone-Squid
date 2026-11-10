CREATE TABLE public.build_schematics (
    id bigint NOT NULL,
    build_id bigint NOT NULL,
    file_sha256 text NOT NULL,
    is_primary boolean NOT NULL,
    original_filename text,
    width integer NOT NULL,
    height integer NOT NULL,
    length integer NOT NULL,
    allocated_width integer NOT NULL,
    allocated_height integer NOT NULL,
    allocated_length integer NOT NULL,
    block_count integer NOT NULL,
    bounding_volume bigint NOT NULL,
    entity_count integer NOT NULL,
    palette_size integer NOT NULL,
    region_names text[] NOT NULL,
    source_data_version integer,
    declared_name text,
    declared_author text,
    signs jsonb NOT NULL,
    fingerprint_structural text,
    fingerprint_shape text,
    fingerprint_exact text,
    signature_structural text,
    analyzer_version text NOT NULL,
    analysis_schema_version smallint NOT NULL,
    lattice jsonb,
    analyzed_at timestamp with time zone DEFAULT now() NOT NULL,
    simulation_evidence jsonb,
    visibility text DEFAULT 'legacy_unverified'::text NOT NULL,
    license_code text,
    rights_attested_at timestamp with time zone,
    rights_attested_by_account_id integer,
    published_at timestamp with time zone,
    withdrawn_at timestamp with time zone,
    uploaded_by_account_id integer,
    CONSTRAINT build_schematics_block_count_check CHECK ((block_count <= bounding_volume)),
    CONSTRAINT build_schematics_bounding_volume_check CHECK ((bounding_volume = (((width)::bigint * height) * length))),
    CONSTRAINT build_schematics_license_check CHECK (((license_code IS NULL) OR (license_code = ANY (ARRAY['cc0_1_0'::text, 'cc_by_4_0'::text, 'cc_by_sa_4_0'::text, 'cc_by_nd_4_0'::text, 'cc_by_nc_4_0'::text, 'cc_by_nc_sa_4_0'::text, 'cc_by_nc_nd_4_0'::text])))),
    CONSTRAINT build_schematics_measurements_nonnegative CHECK (((width >= 0) AND (height >= 0) AND (length >= 0) AND (allocated_width >= 0) AND (allocated_height >= 0) AND (allocated_length >= 0) AND (block_count >= 0) AND (entity_count >= 0) AND (palette_size >= 0))),
    CONSTRAINT build_schematics_public_is_published CHECK (((visibility <> 'public_download'::text) OR (published_at IS NOT NULL))),
    CONSTRAINT build_schematics_publication_complete CHECK (((visibility <> 'public_download'::text) OR ((license_code IS NOT NULL) AND (rights_attested_at IS NOT NULL) AND (rights_attested_by_account_id IS NOT NULL)))),
    CONSTRAINT build_schematics_tight_within_allocated CHECK (((width <= allocated_width) AND (height <= allocated_height) AND (length <= allocated_length))),
    CONSTRAINT build_schematics_visibility_check CHECK ((visibility = ANY (ARRAY['legacy_unverified'::text, 'reviewer_only'::text, 'public_download'::text]))),
    CONSTRAINT build_schematics_withdrawn_after_published CHECK (((withdrawn_at IS NULL) OR ((published_at IS NOT NULL) AND (withdrawn_at >= published_at))))
);

COMMENT ON TABLE public.build_schematics IS 'One analyzed file attached to a build, with its publication policy.

Measurements and fingerprints are stored here for duplicate shortlisting. Fingerprint lookups
include analyzer_version because an engine upgrade can change the hashes. Reanalyzing an
attachment updates this evidence without changing its source bytes.';

COMMENT ON COLUMN public.build_schematics.file_sha256 IS 'Digest of the analyzed bytes, which may be shared by several builds.

Deletion is restricted while any attachment references the file.';

COMMENT ON COLUMN public.build_schematics.is_primary IS 'Featured attachment used for generated previews, conversion, and timing measurements.';

COMMENT ON COLUMN public.build_schematics.original_filename IS 'Original display filename; ingestion may also use its extension as a fallback format hint.';

COMMENT ON COLUMN public.build_schematics.width IS 'Tight content width. Machine-read, unlike the human-declared value on `builds`.';

COMMENT ON COLUMN public.build_schematics.allocated_width IS 'Width of the region the file allocates, which can far exceed the tight content.';

COMMENT ON COLUMN public.build_schematics.block_count IS 'Non-air block count, separate from the Door Rules cumulative volume.

The latter includes air pockets and needs hallway, frame, and hitbox review.';

COMMENT ON COLUMN public.build_schematics.bounding_volume IS 'Tight bounding box volume including air. Materialised so it can be range-scanned.';

COMMENT ON COLUMN public.build_schematics.palette_size IS 'How many distinct block states the file declares.';

COMMENT ON COLUMN public.build_schematics.source_data_version IS 'The Minecraft data version the file declares, or `None` when it declares none.';

COMMENT ON COLUMN public.build_schematics.signs IS 'Sign text recovered from the schematic, as `{x, y, z, text}` objects.';

COMMENT ON COLUMN public.build_schematics.fingerprint_structural IS 'Coarse translation-invariant hash used to shortlist files for pairwise comparison.

Matching values can include different block arrangements and do not establish duplication.';

COMMENT ON COLUMN public.build_schematics.fingerprint_shape IS 'Translation- and rotation-invariant identity. This is the primary duplicate index.';

COMMENT ON COLUMN public.build_schematics.fingerprint_exact IS 'Material- and orientation-sensitive identity, the strict tier.';

COMMENT ON COLUMN public.build_schematics.signature_structural IS 'The engine''s structural signature document, kept for pre-filter experiments.';

COMMENT ON COLUMN public.build_schematics.analyzer_version IS 'Engine version that produced these fingerprints; comparisons must use the same version.';

COMMENT ON COLUMN public.build_schematics.analysis_schema_version IS 'Application analysis format version, independent of the engine version.';

COMMENT ON COLUMN public.build_schematics.lattice IS 'The highest-coverage repeating unit cell found, if the build has one.';

COMMENT ON COLUMN public.build_schematics.simulation_evidence IS 'Staff-triggered tick-engine evidence. It never changes the build''s declared timing.';

COMMENT ON COLUMN public.build_schematics.visibility IS 'Explicit download choice. Existing attachments remain private until re-attested.';

COMMENT ON COLUMN public.build_schematics.uploaded_by_account_id IS 'Account that supplied the file, which can differ from the account attesting publication rights.';

ALTER TABLE ONLY public.build_schematics ALTER COLUMN id SET DEFAULT nextval('public.build_schematics_id_seq'::regclass);

ALTER TABLE ONLY public.build_schematics
    ADD CONSTRAINT build_schematics_build_file_key UNIQUE (build_id, file_sha256);

ALTER TABLE ONLY public.build_schematics
    ADD CONSTRAINT build_schematics_pkey PRIMARY KEY (id);

CREATE INDEX build_schematics_block_count_idx ON public.build_schematics USING btree (block_count);

CREATE INDEX build_schematics_build_id_idx ON public.build_schematics USING btree (build_id);

CREATE INDEX build_schematics_file_sha256_idx ON public.build_schematics USING btree (file_sha256);

CREATE INDEX build_schematics_fingerprint_shape_idx ON public.build_schematics USING btree (fingerprint_shape, analyzer_version) WHERE (fingerprint_shape IS NOT NULL);

CREATE INDEX build_schematics_fingerprint_structural_idx ON public.build_schematics USING btree (fingerprint_structural, analyzer_version) WHERE (fingerprint_structural IS NOT NULL);

CREATE UNIQUE INDEX build_schematics_one_primary_per_build ON public.build_schematics USING btree (build_id) WHERE is_primary;

CREATE INDEX build_schematics_public_download_idx ON public.build_schematics USING btree (build_id, id) WHERE ((visibility = 'public_download'::text) AND (withdrawn_at IS NULL));

CREATE INDEX build_schematics_rights_attested_by_idx ON public.build_schematics USING btree (rights_attested_by_account_id);

CREATE INDEX build_schematics_uploaded_by_idx ON public.build_schematics USING btree (uploaded_by_account_id);

CREATE TRIGGER build_schematics_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.build_schematics FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER build_schematics_reject_truncate BEFORE TRUNCATE ON public.build_schematics FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

ALTER TABLE ONLY public.build_schematics
    ADD CONSTRAINT build_schematics_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.build_schematics
    ADD CONSTRAINT build_schematics_file_sha256_fkey FOREIGN KEY (file_sha256) REFERENCES public.schematic_files(sha256) ON DELETE RESTRICT;

ALTER TABLE ONLY public.build_schematics
    ADD CONSTRAINT build_schematics_rights_attested_by_account_id_fkey FOREIGN KEY (rights_attested_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.build_schematics
    ADD CONSTRAINT build_schematics_uploaded_by_account_id_fkey FOREIGN KEY (uploaded_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;

