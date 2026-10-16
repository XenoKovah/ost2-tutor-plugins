SELECT ev.id
FROM openedx.student_courseenrollment ce
JOIN (SELECT id, history_user_id, ROW_NUMBER() OVER (PARTITION BY id ORDER BY history_date, history_id) AS rn
      FROM openedx.student_courseenrollment_history) f ON f.id = ce.id AND f.rn = 1
JOIN openedx.auth_user u ON u.id = ce.user_id
JOIN rgg.events_event ev ON ev.user_uid = CONVERT(u.username USING utf8mb4) COLLATE utf8mb4_unicode_ci
                        AND ev.course_id = CONVERT(ce.course_id USING utf8mb4) COLLATE utf8mb4_unicode_ci
                        AND DATE(ev.created_at) = DATE(ce.created)
JOIN rgg.events_eventconfiguration c ON c.id = ev.configuration_id
JOIN rgg.events_eventtype t ON t.id = c.event_type_id AND t.name = 'edx_course_enrollment_activated'
WHERE f.history_user_id IS NULL OR f.history_user_id <> ce.user_id;
