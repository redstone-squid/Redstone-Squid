CREATE SEQUENCE public.creator_alias_claims_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.creator_alias_claims_id_seq OWNED BY public.creator_alias_claims.id;

