-- Pre-deploy check for migration fk.0025_video_file_variant.
--
-- 0025 drops the fk_fileformat table and moves each file's kind onto
-- fk_videofile.variant, taking vod_publish and mime_type into the
-- VideoFileVariant enum in code. It refuses to run if the database says
-- anything the enum does not, since choices were never enforced by the
-- database and a row could have been edited through the admin.
--
-- Run this against production first. Zero rows means 0025 will apply.
-- Any row is something to correct (in the data, or in the enum) before
-- deploying; the migration would stop on the same finding.

WITH expected(fsname, vod_publish, mime_type) AS (
    VALUES ('large_thumb',   false, NULL::text),
           ('broadcast',     false, NULL),
           ('vc1',           false, NULL),
           ('med_thumb',     false, NULL),
           ('small_thumb',   false, NULL),
           ('original',      false, NULL),
           ('theora',        true,  'video/ogg'),
           ('srt',           false, NULL),
           ('cloudflare_id', false, NULL),
           ('dash',          false, 'application/dash+xml'),
           ('webm_med',      false, NULL)
)
SELECT f.fsname,
       (SELECT count(*) FROM fk_videofile vf WHERE vf.format_id = f.id) AS files,
       CASE
           WHEN e.fsname IS NULL
               THEN 'name is not in VideoFileVariant'
           WHEN f.vod_publish IS DISTINCT FROM e.vod_publish
               THEN format('vod_publish is %s, the enum says %s', f.vod_publish, e.vod_publish)
           ELSE format('mime_type is %L, the enum says %L',
                       nullif(f.mime_type, ''), e.mime_type)
       END AS would_stop_the_migration
FROM fk_fileformat f
LEFT JOIN expected e ON e.fsname = f.fsname
WHERE e.fsname IS NULL
   OR f.vod_publish IS DISTINCT FROM e.vod_publish
   -- An unset mime type is NULL from the fixture and '' from the admin;
   -- the migration treats both as "nothing said", so this must too.
   OR nullif(f.mime_type, '') IS DISTINCT FROM e.mime_type
ORDER BY f.fsname;

-- Optional: the distribution 0025 will reproduce in fk_videofile.variant.
-- Re-run afterwards as `SELECT variant, count(*) FROM fk_videofile
-- GROUP BY 1 ORDER BY 2 DESC;` and compare.
--
--   SELECT ff.fsname AS variant, count(*)
--   FROM fk_videofile vf
--   JOIN fk_fileformat ff ON ff.id = vf.format_id
--   GROUP BY 1 ORDER BY 2 DESC;
