CREATE POLICY Enable insert for authenticated users only ON public.versions FOR INSERT TO authenticated,
service_role USING (true)
WITH
  CHECK (true);

;

CREATE POLICY Enable read access for all users ON public.versions FOR
SELECT
  TO public USING (true)
WITH
  CHECK (true);

;