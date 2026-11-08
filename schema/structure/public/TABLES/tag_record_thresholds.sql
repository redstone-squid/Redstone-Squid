CREATE TABLE public.tag_record_thresholds (
    tag_id bigint NOT NULL,
    value_type text NOT NULL,
    numeric_value numeric NOT NULL,
    display_order integer NOT NULL,
    CONSTRAINT tag_record_thresholds_numeric_check CHECK ((value_type = 'numeric'::text))
);

COMMENT ON TABLE public.tag_record_thresholds IS 'A staff-seeded eager threshold for a parameterized restriction.';

ALTER TABLE ONLY public.tag_record_thresholds
    ADD CONSTRAINT tag_record_thresholds_pkey PRIMARY KEY (tag_id, numeric_value);

ALTER TABLE ONLY public.tag_record_thresholds
    ADD CONSTRAINT tag_record_thresholds_definition_value_fkey FOREIGN KEY (tag_id, value_type) REFERENCES public.tag_definitions(id, value_type) ON DELETE CASCADE;

