CREATE POLICY Enable insert for authenticated users only ON public.restriction_aliases FOR INSERT TO authenticated USING (true)
WITH
  CHECK (true);

;

CREATE POLICY Enable read access for all users ON public.restriction_aliases FOR
SELECT
  TO public USING (true)
WITH
  CHECK (true);

;