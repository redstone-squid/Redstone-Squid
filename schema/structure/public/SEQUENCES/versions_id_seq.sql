CREATE SEQUENCE public.versions_id_seq
    AS smallint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.versions_id_seq OWNED BY public.versions.id;

