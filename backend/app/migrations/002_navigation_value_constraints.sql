UPDATE vessels
SET speed_over_ground_knots = NULL
WHERE speed_over_ground_knots NOT BETWEEN 0 AND 102.2;

UPDATE vessels
SET course_over_ground_degrees = NULL
WHERE course_over_ground_degrees NOT BETWEEN 0 AND 359.9;

UPDATE vessels
SET true_heading_degrees = NULL
WHERE true_heading_degrees NOT BETWEEN 0 AND 359;

UPDATE vessels
SET navigational_status = NULL
WHERE navigational_status NOT BETWEEN 0 AND 15;

UPDATE position_reports
SET speed_over_ground_knots = NULL
WHERE speed_over_ground_knots NOT BETWEEN 0 AND 102.2;

UPDATE position_reports
SET course_over_ground_degrees = NULL
WHERE course_over_ground_degrees NOT BETWEEN 0 AND 359.9;

UPDATE position_reports
SET true_heading_degrees = NULL
WHERE true_heading_degrees NOT BETWEEN 0 AND 359;

UPDATE position_reports
SET navigational_status = NULL
WHERE navigational_status NOT BETWEEN 0 AND 15;

ALTER TABLE vessels
    ADD CONSTRAINT vessels_sog_valid
        CHECK (speed_over_ground_knots BETWEEN 0 AND 102.2),
    ADD CONSTRAINT vessels_cog_valid
        CHECK (course_over_ground_degrees BETWEEN 0 AND 359.9),
    ADD CONSTRAINT vessels_heading_valid
        CHECK (true_heading_degrees BETWEEN 0 AND 359),
    ADD CONSTRAINT vessels_navigation_status_valid
        CHECK (navigational_status BETWEEN 0 AND 15);

ALTER TABLE position_reports
    ADD CONSTRAINT position_reports_sog_valid
        CHECK (speed_over_ground_knots BETWEEN 0 AND 102.2),
    ADD CONSTRAINT position_reports_cog_valid
        CHECK (course_over_ground_degrees BETWEEN 0 AND 359.9),
    ADD CONSTRAINT position_reports_heading_valid
        CHECK (true_heading_degrees BETWEEN 0 AND 359),
    ADD CONSTRAINT position_reports_navigation_status_valid
        CHECK (navigational_status BETWEEN 0 AND 15);

