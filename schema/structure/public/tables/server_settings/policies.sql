CREATE POLICY Enable insert for authenticated users only ON public.server_settings FOR INSERT TO authenticated USING (true)
WITH
  CHECK (true);

;