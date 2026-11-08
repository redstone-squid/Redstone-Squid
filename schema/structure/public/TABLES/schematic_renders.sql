CREATE TABLE public.schematic_renders (
    id bigint NOT NULL,
    build_schematic_id bigint NOT NULL,
    recipe_hash text NOT NULL,
    url text NOT NULL,
    width integer NOT NULL,
    height integer NOT NULL,
    byte_size integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    object_key text,
    CONSTRAINT schematic_renders_sizes_positive CHECK (((width > 0) AND (height > 0) AND (byte_size > 0)))
);

COMMENT ON TABLE public.schematic_renders IS 'A replaceable preview artifact keyed by the complete rendering recipe.';

COMMENT ON COLUMN public.schematic_renders.recipe_hash IS 'Hash of the source and pack digests, framing, schematic dimensions, and analyzer version.';

ALTER TABLE ONLY public.schematic_renders ALTER COLUMN id SET DEFAULT nextval('public.schematic_renders_id_seq'::regclass);

ALTER TABLE ONLY public.schematic_renders
    ADD CONSTRAINT schematic_renders_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.schematic_renders
    ADD CONSTRAINT schematic_renders_schematic_recipe_key UNIQUE (build_schematic_id, recipe_hash);

ALTER TABLE ONLY public.schematic_renders
    ADD CONSTRAINT schematic_renders_build_schematic_id_fkey FOREIGN KEY (build_schematic_id) REFERENCES public.build_schematics(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.schematic_renders
    ADD CONSTRAINT schematic_renders_object_key_fkey FOREIGN KEY (object_key) REFERENCES public.schematic_preview_objects(object_key) ON DELETE RESTRICT;

