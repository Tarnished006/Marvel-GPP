"""
tests/test_report_redesign.py -- Validation suite for the redesigned Aegis-Touch report generation.
"""

import os
import sys
import re
import unittest
import numpy as np

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

from report_export import build_case_report, _deduplicate_measurements


class TestReportRedesign(unittest.TestCase):
    def setUp(self):
        self.out_dir = os.path.join(os.path.dirname(__file__), "test_reports")
        os.makedirs(self.out_dir, exist_ok=True)

    def _get_page_boxes(self, pdf_path: str) -> list[tuple[float, float]]:
        """Extracts MediaBox dimensions (width_pt, height_pt) from raw PDF file."""
        with open(pdf_path, "rb") as f:
            content = f.read().decode("latin1")
        # Match /MediaBox [ 0 0 595.276 841.89 ] or /MediaBox [0 0 595.275552 841.889736]
        matches = re.findall(r"/MediaBox\s*\[\s*0(?:\.0+)?\s+0(?:\.0+)?\s+([\d.]+)\s+([\d.]+)\s*\]", content)
        return [(float(w), float(h)) for w, h in matches]

    def test_01_empty_metadata_and_no_measurements(self):
        """Report must succeed and handle empty metadata and no measurements safely."""
        path = build_case_report(
            patient={},
            scan={},
            measurements=[],
            notes=[],
            out_dir=self.out_dir,
        )
        self.assertTrue(os.path.isfile(path))
        boxes = self._get_page_boxes(path)
        self.assertGreaterEqual(len(boxes), 1)
        for w, h in boxes:
            # 595.28 pt x 841.89 pt is standard A4 (210 mm x 297 mm)
            self.assertAlmostEqual(w, 595.28, delta=1.5)
            self.assertAlmostEqual(h, 841.89, delta=1.5)

    def test_02_bool_and_malformed_inputs_no_crash(self):
        """Verifies fix for AttributeError: 'bool' object has no attribute 'get'."""
        try:
            path = build_case_report(
                patient=False,      # Bool instead of dict!
                scan=True,          # Bool instead of dict!
                measurements=False, # Bool instead of list!
                notes=False,        # Bool instead of list!
                metadata=False,     # Bool instead of dict!
                view_state=False,   # Bool instead of dict!
                images=False,       # Bool instead of list!
                out_dir=self.out_dir,
            )
            self.assertTrue(os.path.isfile(path))
        except AttributeError as exc:
            self.fail(f"Crashed with AttributeError on bool input: {exc}")

    def test_03_single_measurement_with_rich_metadata(self):
        """Single measurement with rich DICOM metadata and view state."""
        patient = {
            "name": "Jane Doe",
            "mrn": "MRN-10928",
            "age": "54",
            "sex": "Female",
            "dob": "1972-04-15",
            "allergies": "Penicillin",
            "blood_type": "A+",
        }
        scan = {
            "type": "CT Chest / Abdomen",
            "date": "2026-09-18",
            "file_path": "/data/dicom/CT_CHEST_001",
            "slice_count": 226,
        }
        metadata = {
            "modality": "CT",
            "series_description": "Thoracic Spine Bone Rec",
            "body_part": "CHEST",
            "num_slices": "226 slices",
            "dimensions": "512 × 512 × 226 voxels",
            "pixel_spacing": "0.70 mm × 0.70 mm (dy × dx)",
            "slice_thickness": "1.25 mm",
            "spacing_between_slices": "1.25 mm",
            "volume_dimensions_mm": "358.4 × 358.4 × 282.5 mm³",
            "rescale_slope": "1.00",
            "rescale_intercept": "-1024.0 HU",
            "orientation_info": "Axial (Standard Transverse)",
            "hu_min": "-1024.0 HU",
            "hu_max": "+2890.0 HU",
            "current_window_width": "1800 HU",
            "current_window_level": "400 HU",
            "current_wl_preset": "Bone",
        }
        view_state = {
            "orientation": "Axial",
            "slice_index": "Slice 92 / 226",
            "physical_pos": "X: 179.2 mm, Y: 179.2 mm, Z: 115.0 mm",
            "window_width": "1800 HU",
            "window_level": "400 HU",
            "preset": "Bone",
            "opacity_pct": "100%",
            "render_mode": "Surface Mesh (Marching Cubes)",
            "heatmap_mode": "Disabled (Normal Bone)",
            "clipping_mode": "Enabled (Y-axis @ 50%)",
            "clip_axis": "Y-axis",
            "clip_fraction": "50%",
            "clip_inverted": False,
            "sync_mode": "Enabled (2D↔3D Synchronized)",
            "cursor_hu": "+420 HU",
        }
        measurements = [
            {
                "plane": "Axial / 3D synchronized",
                "kind": "distance",
                "value": 78.9,
                "unit": "mm",
                "slice_idx": "Slice 92",
                "p1": (124.5, 180.2, 115.0),
                "p2": (198.3, 162.0, 115.0),
                "source": "2D",
            }
        ]
        notes = [
            {
                "created_at": "2026-09-18 14:30",
                "author": "Dr. Smith",
                "content": "Calibrated measurement across thoracic pedicle confirms 78.9 mm physical distance. No clipping artifacts detected.",
            }
        ]

        path = build_case_report(
            patient=patient,
            scan=scan,
            measurements=measurements,
            notes=notes,
            metadata=metadata,
            view_state=view_state,
            out_dir=self.out_dir,
        )
        self.assertTrue(os.path.isfile(path))
        boxes = self._get_page_boxes(path)
        for w, h in boxes:
            self.assertAlmostEqual(w, 595.28, delta=1.5)
            self.assertAlmostEqual(h, 841.89, delta=1.5)

    def test_04_measurement_deduplication(self):
        """Synchronized 2D and 3D measurements must be deduplicated into ONE entry."""
        # 2D measurement
        m2d = {
            "plane": "2D Axial / 3D synchronized",
            "kind": "distance",
            "value": 78.9,
            "unit": "mm",
            "slice_idx": "Slice 92",
            "p1": (124.5, 180.2, 115.0),
            "p2": (198.3, 162.0, 115.0),
            "source": "2D",
        }
        # Synced 3D measurement corresponding to the same physical points
        m3d_synced = {
            "plane": "3D Volume Space",
            "kind": "distance",
            "value": 78.9,
            "unit": "mm",
            "slice_idx": "3D Space",
            "p1": (124.5, 180.2, 115.0),
            "p2": (198.3, 162.0, 115.0),
            "source": "2D_synced",
        }
        # Distinct independent 3D measurement
        m3d_distinct = {
            "plane": "3D Volume Space",
            "kind": "distance",
            "value": 45.2,
            "unit": "mm",
            "slice_idx": "3D Space",
            "p1": (10.0, 20.0, 30.0),
            "p2": (50.0, 20.0, 30.0),
            "source": "3D",
        }

        deduped = _deduplicate_measurements([m2d, m3d_synced, m3d_distinct])
        self.assertEqual(len(deduped), 2, f"Expected 2 deduplicated measurements, got {len(deduped)}")
        values = [d["value"] for d in deduped]
        self.assertIn(78.9, values)
        self.assertIn(45.2, values)

    def test_05_multiple_measurements_natural_pagination(self):
        """Multiple measurements must naturally paginate across multiple A4 pages without overflowing."""
        measurements = []
        for i in range(1, 15):
            measurements.append({
                "plane": f"Axial Slice {i * 10}",
                "kind": "distance",
                "value": 25.0 + i * 3.5,
                "unit": "mm",
                "slice_idx": f"Slice {i * 10}",
                "p1": (10.0 * i, 20.0, 50.0),
                "p2": (10.0 * i, 55.0, 50.0),
                "source": "2D",
            })

        path = build_case_report(
            patient={"name": "Multi-Measurement Test", "mrn": "TEST-MULTI"},
            measurements=measurements,
            out_dir=self.out_dir,
        )
        self.assertTrue(os.path.isfile(path))
        boxes = self._get_page_boxes(path)
        self.assertGreater(len(boxes), 1, "Report with 14 measurements should span at least 2 pages")
        for idx, (w, h) in enumerate(boxes, 1):
            self.assertAlmostEqual(w, 595.28, delta=1.5, msg=f"Page {idx} width is not A4")
            self.assertAlmostEqual(h, 841.89, delta=1.5, msg=f"Page {idx} height is not A4")

    def test_06_with_mock_image_captures(self):
        """Verifies image embeds stay strictly within A4 boundaries."""
        import matplotlib.pyplot as plt
        # Create a sample test image
        img_path = os.path.join(self.out_dir, "sample_test_capture.png")
        fig, ax = plt.subplots(figsize=(4, 3))
        ax.plot([0, 1, 2], [0, 1, 0])
        ax.set_title("Mock Capture")
        fig.savefig(img_path)
        plt.close(fig)

        images = [
            {"path": img_path, "caption": "Figure 1 — 3D Reconstruction", "title": "3D View"},
            {"path": img_path, "caption": "Figure 2 — 2D CT Slice", "title": "2D View"},
        ]

        path = build_case_report(
            patient={"name": "Image Embed Test", "mrn": "TEST-IMG"},
            images=images,
            out_dir=self.out_dir,
        )
        self.assertTrue(os.path.isfile(path))
        boxes = self._get_page_boxes(path)
        for w, h in boxes:
            self.assertAlmostEqual(w, 595.28, delta=1.5)
            self.assertAlmostEqual(h, 841.89, delta=1.5)


if __name__ == "__main__":
    unittest.main()
