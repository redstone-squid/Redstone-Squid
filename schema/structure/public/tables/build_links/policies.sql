CREATE POLICY Enable insert for authenticated users only ON public.build_links FOR INSERT TO authenticated,
service_role USING (true)
WITH
  CHECK (true);

;