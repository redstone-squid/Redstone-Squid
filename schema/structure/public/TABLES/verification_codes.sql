CREATE TABLE public.verification_codes (
    id bigint NOT NULL,
    minecraft_uuid uuid NOT NULL,
    code text NOT NULL,
    created timestamp with time zone DEFAULT now() NOT NULL,
    expires timestamp with time zone DEFAULT (now() + '00:10:00'::interval) NOT NULL,
    username text NOT NULL,
    valid boolean DEFAULT true NOT NULL,
    reserved_token text,
    reserved_until timestamp with time zone,
    CONSTRAINT verification_codes_reservation_complete CHECK (((reserved_token IS NULL) = (reserved_until IS NULL)))
);

COMMENT ON TABLE public.verification_codes IS 'A verification code for linking Java Edition identities.';

COMMENT ON COLUMN public.verification_codes.reserved_token IS 'Digest of the token holding this code while its consent prompt is open.';

COMMENT ON COLUMN public.verification_codes.reserved_until IS 'When the reservation expires and the code becomes available again.';

ALTER TABLE ONLY public.verification_codes ALTER COLUMN id SET DEFAULT nextval('public.verification_codes_id_seq'::regclass);

ALTER TABLE ONLY public.verification_codes
    ADD CONSTRAINT verification_codes_pkey PRIMARY KEY (id);

CREATE UNIQUE INDEX verification_codes_valid_code_key ON public.verification_codes USING btree (code) WHERE valid;

