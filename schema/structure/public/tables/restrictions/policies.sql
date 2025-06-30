CREATE POLICY Enable insert for authenticated users only ON public.restrictions FOR INSERT TO authenticated USING (true)
WITH
  CHECK (true);

;

CREATE POLICY Enable read access for all users ON public.restrictions FOR
SELECT
  TO public USING (true)
WITH
  CHECK (true);

;