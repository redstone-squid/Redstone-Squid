"""Live message ownership, payloads, and publication.

A root coordinates rendering and interaction dispatch. Candidate publication owns
which generation is visible; message authorities and continuations own delivery
credentials. Scheduling and construction defaults share this message lifetime.
"""
