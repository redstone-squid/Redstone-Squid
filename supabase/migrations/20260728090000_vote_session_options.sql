BEGIN;

CREATE TABLE public.vote_session_options (
    vote_session_id bigint NOT NULL
        REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE,
    emoji text NOT NULL,
    choice text NOT NULL,
    multiplier double precision NOT NULL DEFAULT 1.0,
    position smallint NOT NULL,
    PRIMARY KEY (vote_session_id, emoji),
    UNIQUE (vote_session_id, position),
    CONSTRAINT vote_session_options_choice_check
        CHECK (choice IN ('approve', 'deny')),
    CONSTRAINT vote_session_options_multiplier_check
        CHECK (
            multiplier > 0
            AND multiplier != 'Infinity'::double precision
            AND multiplier != 'NaN'::double precision
        ),
    CONSTRAINT vote_session_options_position_check
        CHECK (position >= 0)
);

ALTER TABLE public.vote_session_options ENABLE ROW LEVEL SECURITY;

INSERT INTO public.vote_session_options (vote_session_id, emoji, choice, multiplier, position)
SELECT vote_sessions.id, defaults.emoji, defaults.choice, 1.0, defaults.position
FROM public.vote_sessions
CROSS JOIN (
    VALUES
        ('👍', 'approve', 0),
        ('✅', 'approve', 1),
        ('👎', 'deny', 2),
        ('❌', 'deny', 3)
) AS defaults(emoji, choice, position);

COMMENT ON TABLE public.vote_session_options IS
    'Ordered reaction options and positive weight multipliers captured for each vote session.';

COMMIT;
