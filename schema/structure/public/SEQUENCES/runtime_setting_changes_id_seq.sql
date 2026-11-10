CREATE SEQUENCE public.runtime_setting_changes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.runtime_setting_changes_id_seq OWNED BY public.runtime_setting_changes.id;

