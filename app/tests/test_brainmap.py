import pytest

from brainhub.brainmap import (add_node, default_map, ensure_folders, hierarchy, load_map,
                               save_map, slugify, validate_map)


def test_slugify():
    assert slugify("Robot Arm") == "robot-arm"
    assert slugify("Proiectare Ș.I.") == "proiectare-s-i"

def test_validate_rejects_duplicate_and_bad_slugs():
    m = default_map(); m.projects = [{"slug":"x","name":"X","is_company":False,"folders":[]}]*2
    assert any("duplicate" in e for e in validate_map(m))
    m.projects = [{"slug":"Bad Slug","name":"B","is_company":False,"folders":[]}]
    assert validate_map(m)

def test_validate_at_most_one_company():
    m = default_map()
    m.projects = [{"slug":s,"name":s,"is_company":True,"folders":[]} for s in ("a","b")]
    assert any("company" in e for e in validate_map(m))

def test_person_must_reference_known_projects():
    m = default_map(); m.people = [{"slug":"ana","name":"Ana","projects":["ghost"]}]
    assert validate_map(m)

def test_save_load_roundtrip_and_folders(tmp_path):
    m = add_node(default_map(), "project", "Robot Arm", None)
    save_map(tmp_path, m); ensure_folders(tmp_path, m)
    assert load_map(tmp_path) == m
    assert (tmp_path/"projects"/"robot-arm").is_dir() and (tmp_path/"_items").is_dir()

def test_add_node_duplicate_raises():
    m = add_node(default_map(), "course", "Fizica", None)
    with pytest.raises(ValueError): add_node(m, "course", "Fizica", None)

def test_hierarchy_counts(tmp_path):
    m = add_node(default_map(), "project", "robotarm", None); ensure_folders(tmp_path, m)
    (tmp_path/"projects"/"robotarm"/"notes.md").write_text("x", encoding="utf-8")
    tree = hierarchy(tmp_path, m, {"robotarm": 3})
    node = next(c for c in tree["children"] if c["slug"]=="projects")["children"][0]
    assert node["files"] == 1 and node["items"] == 3
