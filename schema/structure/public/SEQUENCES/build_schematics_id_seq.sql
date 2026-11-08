CREATE SEQUENCE public.build_schematics_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.build_schematics_id_seq OWNED BY public.build_schematics.id;

