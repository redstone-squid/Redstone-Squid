CREATE POLICY Enable insert for authenticated users only ON public.builds FOR INSERT TO authenticated,
service_role USING (true)
WITH
  CHECK (true);

;

CREATE POLICY Enable read access for all users ON public.builds FOR
SELECT
  TO public USING (true)
WITH
  CHECK (true);

;