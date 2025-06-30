CREATE POLICY Enable insert for authenticated users only ON public.votes FOR INSERT TO authenticated USING (true)
WITH
  CHECK (true);

;