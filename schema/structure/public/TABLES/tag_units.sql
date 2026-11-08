CREATE TABLE public.tag_units (
    key text NOT NULL,
    dimension text NOT NULL,
    symbol text NOT NULL,
    aliases text[] NOT NULL,
    scale_to_base numeric NOT NULL
);

COMMENT ON TABLE public.tag_units IS 'A unit accepted by numeric tag inputs.';

ALTER TABLE ONLY public.tag_units
    ADD CONSTRAINT tag_units_pkey PRIMARY KEY (key);

