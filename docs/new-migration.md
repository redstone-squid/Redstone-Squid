1. `supabase migration new <name>`
2. Write the SQL manually, or use the studio to make the changes then run `supabase db diff`
3. If changed column names, update `seed.sql`, and all the references in the code base
4. `supabase migration up` (Push migration file to local db, may fail if you already ran the migration locally)
5. `supabase db push` (Push changes to remote)
6. restart the bot