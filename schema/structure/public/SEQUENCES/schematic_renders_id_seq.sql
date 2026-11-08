CREATE SEQUENCE public.schematic_renders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.schematic_renders_id_seq OWNED BY public.schematic_renders.id;

