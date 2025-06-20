insert into server_settings(server_id, smallest_channel_id, fastest_channel_id, first_channel_id, builds_channel_id, voting_channel_id, in_server, staff_roles_ids, trusted_roles_ids)
values
(433618741528625152, 536004554743873556, 536004554743873556, 536004554743873556, 536004554743873556, 536004554743873556, TRUE, ARRAY[525738486637002762], ARRAY[525738486637002762]);

insert into types(build_category, name)
values
('Door', 'Regular'),
('Door', 'Funnel'),
('Door', 'Asdjke'),
('Door', 'Cave'),
('Door', 'Corner'),
('Door', 'Dual Cave Corner'),
('Door', 'Staircase'),
('Door', 'Gold Play Button'),
('Door', 'Vortex'),
('Door', 'Pitch'),
('Door', 'Bar'),
('Door', 'Vertical'),
('Door', 'Yaw'),
('Door', 'Reversed'),
('Door', 'Inverted'),
('Door', 'Dual'),
('Door', 'Vault'),
('Door', 'Iris'),
('Door', 'Onion'),
('Door', 'Stargate'),
('Door', 'Full Lamp'),
('Door', 'Lamp'),
('Door', 'Hidden Lamp'),
('Door', 'Sissy Bar'),
('Door', 'Checkerboard'),
('Door', 'Windows'),
('Door', 'Redstone Block Center'),
('Door', 'Sand'),
('Door', 'Glass Stripe'),
('Door', 'Center Glass'),
('Door', 'Always On Lamp'),
('Door', 'Circle'),
('Door', 'Triangle'),
('Door', 'Right Triangle'),
('Door', 'Banana'),
('Door', 'Diamond'),
('Door', 'Slab-Shifted'),
('Door', 'Rail'),
('Door', 'Dual Rail'),
('Door', 'Carpet'),
('Door', 'Semi TNT'),
('Door', 'Full TNT');

insert into restrictions(build_category, name, type)
values
('Door', 'Super Seamless', 'wiring-placement'),
('Door', 'Full Seamless', 'wiring-placement'),
('Door', 'Semi Seamless', 'wiring-placement'),
('Door', 'Quart Seamless', 'wiring-placement'),
('Door', 'Dentless', 'wiring-placement'),
('Door', 'Full Trapdoor', 'wiring-placement'),
('Door', 'Flush', 'wiring-placement'),
('Door', 'Deluxe', 'wiring-placement'),
('Door', 'Flush Layout', 'wiring-placement'),
('Door', 'Semi Flush', 'wiring-placement'),
('Door', 'Semi Deluxe', 'wiring-placement'),
('Door', 'Full Floor Hipster', 'wiring-placement'),
('Door', 'Full Ceiling Hipster', 'wiring-placement'),
('Door', 'Full Wall Hipster', 'wiring-placement'),
('Door', 'Semi Floor Hipster', 'wiring-placement'),
('Door', 'Semi Ceiling Hipster', 'wiring-placement'),
('Door', 'Semi Wall Hipster', 'wiring-placement'),
('Door', 'Expandable', 'wiring-placement'),
('Door', 'Full Tileable', 'wiring-placement'),
('Door', 'Semi Tileable', 'wiring-placement'),
('Door', 'No Slime Blocks', 'component'),
('Door', 'No Honey Blocks', 'component'),
('Door', 'No Gravity Blocks', 'component'),
('Door', 'No Sticky Pistons', 'component'),
('Door', 'Contained Slime Blocks', 'component'),
('Door', 'Contained Honey Blocks', 'component'),
('Door', 'Only Wiring Slime Blocks', 'component'),
('Door', 'Only Wiring Honey Blocks', 'component'),
('Door', 'Only Wiring Gravity Blocks', 'component'),
('Door', 'No Observers', 'component'),
('Door', 'No Note Blocks', 'component'),
('Door', 'No Clocks', 'component'),
('Door', 'No Entities', 'component'),
('Door', 'No Flying Machines', 'component'),
('Door', 'Zomba', 'component'),
('Door', 'Zombi', 'component'),
('Door', 'Torch and Dust Only', 'component'),
('Door', 'Redstone Block Only', 'component'),
('Door', 'Not Locational', 'miscellaneous'),
('Door', 'Not Directional', 'miscellaneous'),
('Door', 'Locational With Fixes', 'miscellaneous'),
('Door', 'Directional With Fixes', 'miscellaneous'),
('Door', 'Locational', 'miscellaneous'),
('Door', 'Directional', 'miscellaneous');

insert into versions (edition, major_version, minor_version, patch_number)
values
('Java', 1, 20, 5),
('Java', 1, 20, 4),
('Java', 1, 20, 3),
('Java', 1, 20, 2),
('Java', 1, 20, 1),
('Java', 1, 20, 0),
('Java', 1, 19, 4),
('Java', 1, 19, 3),
('Java', 1, 19, 2),
('Java', 1, 19, 1),
('Java', 1, 19, 0),
('Java', 1, 18, 2),
('Java', 1, 18, 1),
('Java', 1, 18, 0),
('Java', 1, 17, 1),
('Java', 1, 17, 0),
('Java', 1, 16, 5),
('Java', 1, 16, 4),
('Java', 1, 16, 3),
('Java', 1, 16, 2),
('Java', 1, 16, 1),
('Java', 1, 16, 0),
('Java', 1, 15, 2),
('Java', 1, 15, 1),
('Java', 1, 15, 0),
('Java', 1, 14, 4),
('Java', 1, 14, 3),
('Java', 1, 14, 2),
('Java', 1, 14, 1),
('Java', 1, 14, 0),
('Java', 1, 13, 2),
('Java', 1, 13, 1),
('Java', 1, 13, 0),
('Java', 1, 12, 2),
('Java', 1, 12, 1),
('Java', 1, 12, 0),
('Java', 1, 11, 2),
('Java', 1, 11, 1),
('Java', 1, 11, 0),
('Java', 1, 10, 2),
('Java', 1, 10, 1),
('Java', 1, 10, 0),
('Java', 1, 9, 4),
('Java', 1, 9, 3),
('Java', 1, 9, 2),
('Java', 1, 9, 1),
('Java', 1, 9, 0),
('Java', 1, 8, 9),
('Java', 1, 8, 8),
('Java', 1, 8, 7),
('Java', 1, 8, 6),
('Java', 1, 8, 5),
('Java', 1, 8, 4),
('Java', 1, 8, 3),
('Java', 1, 8, 2),
('Java', 1, 8, 1),
('Java', 1, 8, 0),
('Java', 1, 7, 10),
('Java', 1, 7, 9),
('Java', 1, 7, 8),
('Java', 1, 7, 7),
('Java', 1, 7, 6),
('Java', 1, 7, 5),
('Java', 1, 7, 4),
('Java', 1, 7, 2),
('Java', 1, 6, 4),
('Java', 1, 6, 2),
('Java', 1, 6, 1),
('Java', 1, 5, 2),
('Java', 1, 5, 1),
('Java', 1, 5, 0),
('Java', 1, 4, 7),
('Java', 1, 4, 6),
('Java', 1, 4, 5),
('Java', 1, 4, 4),
('Java', 1, 4, 2),
('Java', 1, 3, 2),
('Java', 1, 3, 1),
('Java', 1, 2, 5),
('Java', 1, 2, 4),
('Java', 1, 2, 3),
('Java', 1, 2, 2),
('Java', 1, 2, 1),
('Java', 1, 1, 0),
('Java', 1, 0, 1),
('Java', 1, 0, 0),
('Bedrock', 1, 20, 80),
('Bedrock', 1, 20, 73),
('Bedrock', 1, 20, 72),
('Bedrock', 1, 20, 71),
('Bedrock', 1, 20, 70),
('Bedrock', 1, 20, 62),
('Bedrock', 1, 20, 60),
('Bedrock', 1, 20, 51),
('Bedrock', 1, 20, 50),
('Bedrock', 1, 20, 41),
('Bedrock', 1, 20, 40),
('Bedrock', 1, 20, 32),
('Bedrock', 1, 20, 31),
('Bedrock', 1, 20, 30),
('Bedrock', 1, 20, 15),
('Bedrock', 1, 20, 14),
('Bedrock', 1, 20, 13),
('Bedrock', 1, 20, 12),
('Bedrock', 1, 20, 10),
('Bedrock', 1, 20, 1),
('Bedrock', 1, 20, 0),
('Bedrock', 1, 19, 83),
('Bedrock', 1, 19, 81),
('Bedrock', 1, 19, 80),
('Bedrock', 1, 19, 73),
('Bedrock', 1, 19, 72),
('Bedrock', 1, 19, 71),
('Bedrock', 1, 19, 70),
('Bedrock', 1, 19, 63),
('Bedrock', 1, 19, 62),
('Bedrock', 1, 19, 60),
('Bedrock', 1, 19, 51),
('Bedrock', 1, 19, 50),
('Bedrock', 1, 19, 41),
('Bedrock', 1, 19, 40),
('Bedrock', 1, 19, 31),
('Bedrock', 1, 19, 30),
('Bedrock', 1, 19, 22),
('Bedrock', 1, 19, 21),
('Bedrock', 1, 19, 20),
('Bedrock', 1, 19, 11),
('Bedrock', 1, 19, 10),
('Bedrock', 1, 19, 2),
('Bedrock', 1, 19, 0),
('Bedrock', 1, 18, 33),
('Bedrock', 1, 18, 32),
('Bedrock', 1, 18, 31),
('Bedrock', 1, 18, 30),
('Bedrock', 1, 18, 12),
('Bedrock', 1, 18, 11),
('Bedrock', 1, 18, 10),
('Bedrock', 1, 18, 2),
('Bedrock', 1, 18, 1),
('Bedrock', 1, 18, 0),
('Bedrock', 1, 17, 41),
('Bedrock', 1, 17, 40),
('Bedrock', 1, 17, 34),
('Bedrock', 1, 17, 33),
('Bedrock', 1, 17, 32),
('Bedrock', 1, 17, 30),
('Bedrock', 1, 17, 11),
('Bedrock', 1, 17, 10),
('Bedrock', 1, 17, 2),
('Bedrock', 1, 17, 1),
('Bedrock', 1, 17, 0),
('Bedrock', 1, 16, 221),
('Bedrock', 1, 16, 220),
('Bedrock', 1, 16, 210),
('Bedrock', 1, 16, 201),
('Bedrock', 1, 16, 200),
('Bedrock', 1, 16, 101),
('Bedrock', 1, 16, 100),
('Bedrock', 1, 16, 61),
('Bedrock', 1, 16, 60),
('Bedrock', 1, 16, 50),
('Bedrock', 1, 16, 42),
('Bedrock', 1, 16, 40),
('Bedrock', 1, 16, 21),
('Bedrock', 1, 16, 20),
('Bedrock', 1, 16, 10),
('Bedrock', 1, 16, 1),
('Bedrock', 1, 16, 0),
('Bedrock', 1, 14, 60),
('Bedrock', 1, 14, 41),
('Bedrock', 1, 14, 30),
('Bedrock', 1, 14, 21),
('Bedrock', 1, 14, 20),
('Bedrock', 1, 14, 1),
('Bedrock', 1, 14, 0),
('Bedrock', 1, 14, 0),
('Bedrock', 1, 13, 3),
('Bedrock', 1, 13, 2),
('Bedrock', 1, 13, 1),
('Bedrock', 1, 13, 0),
('Bedrock', 1, 12, 1),
('Bedrock', 1, 12, 0),
('Bedrock', 1, 11, 4),
('Bedrock', 1, 11, 3),
('Bedrock', 1, 11, 2),
('Bedrock', 1, 11, 1),
('Bedrock', 1, 11, 0),
('Bedrock', 1, 10, 1),
('Bedrock', 1, 10, 0),
('Bedrock', 1, 9, 0),
('Bedrock', 1, 8, 1),
('Bedrock', 1, 8, 0),
('Bedrock', 1, 7, 1),
('Bedrock', 1, 7, 0),
('Bedrock', 1, 6, 2),
('Bedrock', 1, 6, 1),
('Bedrock', 1, 6, 0),
('Bedrock', 1, 5, 3),
('Bedrock', 1, 5, 2),
('Bedrock', 1, 5, 1),
('Bedrock', 1, 5, 0),
('Bedrock', 1, 4, 4),
('Bedrock', 1, 4, 3),
('Bedrock', 1, 4, 2),
('Bedrock', 1, 4, 1),
('Bedrock', 1, 4, 0),
('Bedrock', 1, 2, 16),
('Bedrock', 1, 2, 15),
('Bedrock', 1, 2, 14),
('Bedrock', 1, 2, 13),
('Bedrock', 1, 2, 11),
('Bedrock', 1, 2, 10),
('Bedrock', 1, 2, 9),
('Bedrock', 1, 2, 8),
('Bedrock', 1, 2, 7),
('Bedrock', 1, 2, 6),
('Bedrock', 1, 2, 6),
('Bedrock', 1, 2, 5),
('Bedrock', 1, 2, 3),
('Bedrock', 1, 2, 2),
('Bedrock', 1, 2, 1),
('Bedrock', 1, 2, 0);

INSERT INTO messages ("server_id", "build_id", "channel_id", "id", "updated_at", "purpose", "vote_session_id", "content", "author_id")
VALUES
('433618741528625152', null, '667401499554611210', '1327569309899292754', '2025-01-11 15:06:07+00', 'build_original_message', null, 'some random message', '353089661175988224'),
 ('433618741528625152', null, '667401499554611210', '1327613153755791412', '2025-01-11 15:06:45+00', 'build_original_message', null, 'Fastest 8x8 piston door', '353089661175988224'),
 ('433618741528625152', null, '667401499554611210', '1328158928638443571', '2025-01-13 02:41:21+00', 'build_original_message', null, '# **Smallest 0.9 6x6**', '353089661175988224');

INSERT INTO builds ("id", "submission_status", "edited_time", "record_category", "extra_info", "width", "height", "depth", "completion_time", "submission_time", "category", "submitter_id", "ai_generated", "original_message_id", "version_spec", "embedding")
VALUES
('1', '1', '2025-01-09 15:06:25', 'Smallest', '{"user":"it has egg\\\\nhttps://imgur.com/a/XhVjrzc","unknown_restrictions":{"wiring_placement_restrictions":["Seamless","Flush"]}}', '11', '7', '3', '2023-04-29 11:22 PM EST', '2024-02-28 04:11:00', 'Door', '462848121081167873', 'false', null, null, null),

('2', '0', '2025-01-09 15:07:03', 'Fastest', '{"unknown_restrictions":{"component_restrictions":["Slimeless"],"wiring_placement_restrictions":["Seamless"]}}', '15', '21', '9', null, '2024-04-04 13:41:30', 'Door', '353089661175988224', 'false', null, null, null),

('3', '1', '2025-01-11 15:06:03', 'Fastest', '{"user":"Improved version\\n**size**\\n22x11x4=968b\\n**other info**\\nuncontained cus layout big\\nfound out it was loc and dir, now it''s reliable\\nvideo should be less laggy now :)","unknown_patterns":[],"unknown_restrictions":{"component_restrictions":["Obsless","Entityless"],"miscellaneous_restrictions":["Only piston sounds"],"wiring_placement_restrictions":["Unseamless"]}}', '22', '11', '4', null, '2025-01-11 15:06:04.110194', 'Door', '1159485264570359839', 'true', '1327569309899292754', 'Java 1.21.1', null),

('4', '1', '2025-01-11 15:06:41', 'Fastest', '{"unknown_patterns":[],"unknown_restrictions":{"miscellaneous_restrictions":["Reliable","Entityless"]}}', '23', '16', '8', null, '2025-01-11 15:06:42.09222', 'Door', '1076744341944533053', 'true', '1327613153755791412', 'Java 1.21.1', null),

('5', '1', '2025-01-13 02:41:17', 'Smallest', '{"user":"Finished: Jan 12, 2025\\nNo Input Delay\\nOnly 7 Deep because not cringe","unknown_patterns":[],"unknown_restrictions":{"component_restrictions":["Slimeless"],"miscellaneous_restrictions":["Fully Reliable","Contained","Piston Sounds Only","0.9s"]}}', '16', '7', '18', null, '2025-01-13 02:41:18.030881', 'Door', '689549609605136393', 'true', '1328158928638443571', 'Java 1.21.1', null),

('6', '0', '2025-01-13 04:27:06', 'Smallest', '{"unknown_patterns":[],"unknown_restrictions":{}}', null, null, null, null, '2025-01-13 04:27:06.610537', 'Door', '987131131767959614', 'true', null, 'MCBE', null);

ALTER SEQUENCE submissions_submission_id_seq RESTART WITH 100;
-- Reserve the first 100 submissions for testing data

UPDATE messages SET build_id = '3' WHERE id = '1327569309899292754';
UPDATE messages SET build_id = '4' WHERE id = '1327613153755791412';
UPDATE messages SET build_id = '5' WHERE id = '1328158928638443571';


INSERT INTO doors ("build_id", "orientation", "door_width", "door_height", "door_depth", "normal_opening_time", "normal_closing_time", "visible_opening_time", "visible_closing_time")
VALUES
('3', 'Door', '4', '4', '1', '5', '5', null, null),
('4', 'Door', '8', '8', '1', '27', '21', null, null),
('5', 'Door', '6', '6', '1', '17', '14', null, null),
('6', 'Door', '2', '2', '1', null, null, null, null);

INSERT INTO build_links ("build_id", "url", "media_type")
VALUES
('3', 'https://files.catbox.moe/t09cty.png', 'image'),
('3', 'https://files.catbox.moe/uadbru.mp4', 'video'),
('4', 'https://files.catbox.moe/3rgal1.mp4', 'video'),
('4', 'https://files.catbox.moe/plf7dy.png', 'image'),
('4', 'https://files.catbox.moe/spqr92.mp4', 'video'),
('5', 'https://files.catbox.moe/0eyxaa.png', 'image'),
('5', 'https://files.catbox.moe/0ouxek.png', 'image'),
('5', 'https://imgur.com/a/rGA2DNY', 'image'),
('6', 'https://files.catbox.moe/zpyc13.png', 'image');

INSERT INTO build_restrictions ("build_id", "restriction_id")
VALUES
('3', '7'),
('3', '43'),
('3', '44');

INSERT INTO build_types ("build_id", "type_id")
VALUES
('3', '1'),
('4', '1'),
('5', '1'),
('6', '37');

INSERT INTO build_versions ("build_id", "version_id")
VALUES
('3', '1'),
('4', '1'),
('5', '1');