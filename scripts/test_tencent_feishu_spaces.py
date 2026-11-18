import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("cloud_spaces", Path(__file__).with_name("tencent-feishu-spaces.py"))
cloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)


class SpaceInventoryTest(unittest.TestCase):
    def test_an_unsupported_node_does_not_hide_a_file_or_native_slides(self):
        space, entry = next(iter(cloud.ENTRIES.items()))
        wiki = Mock(SUPPORTED_TYPES={"file"}, identifier=lambda token: token)
        wiki.inventory.return_value = {entry: {"object_type": "mindnote"}, "deck": {"object_type": "file"},
                                       "native": {"object_type": "slides"}}
        client = Mock()
        client.request.side_effect = [
            {"data": {"space": {"name": "shared", "space_id": space}}},
            {"data": {"node": {"node_token": "deck", "space_id": space, "obj_type": "file",
                               "node_type": "origin", "obj_token": "downloadable"}}},
            {"data": {"auth_result": True}}, {"data": {"auth_result": True}},
            {"data": {"node": {"node_token": "native", "space_id": space, "obj_type": "slides",
                               "node_type": "origin", "obj_token": "presentation"}}},
            {"data": {"auth_result": True}}, {"data": {"auth_result": True}},
        ]
        with patch.object(cloud.time, "sleep"):
            result = cloud.inspect_space(wiki, client, space)
        self.assertEqual(result["nodes"], ["deck", "native"])
        self.assertEqual(result["unsupported"], {entry: "mindnote"})
        self.assertEqual([call.args[0].split("?", 1)[1] for call in client.request.call_args_list
                          if "/members/auth?" in call.args[0]],
                         ["type=file&action=view", "type=file&action=export",
                          "type=slides&action=view", "type=slides&action=export"])
