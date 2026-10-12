import re

from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from minitos_core.models import Location
from minitos_core.services import accessible_terminals, terminal_capabilities


class TerminalBrowserTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='terminal-user', password='test-password')
        self.terminal = Location.objects.create(
            name='Browser Terminal',
            location_type=Location.TL,
            map_origin_latitude=41.35,
            map_origin_longitude=2.17,
            map_width_m=1200,
            map_height_m=800,
        )
        Location.objects.create(name='Vessel Location', location_type=Location.VL)

    def test_terminal_browser_requires_login_and_lists_accessible_terminals(self):
        anonymous_response = self.client.get(reverse('terminal_browser'))
        self.assertRedirects(
            anonymous_response,
            f"{reverse('admin:login')}?next={reverse('terminal_browser')}",
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse('terminal_browser'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Browser Terminal')
        self.assertNotContains(response, 'Vessel Location')
        self.assertContains(response, reverse('terminal_editor', args=[self.terminal.id]))
        self.assertContains(response, reverse('berth_plan', args=[self.terminal.id]))
        self.assertContains(response, reverse('che_editor', args=[self.terminal.id]))
        self.assertContains(response, reverse('terminal_editor_new'))

    def test_capability_boundary_is_ready_for_future_authorization(self):
        self.assertFalse(accessible_terminals(AnonymousUser()).exists())
        self.assertEqual(list(accessible_terminals(self.user)), [self.terminal])
        self.assertEqual(
            terminal_capabilities(self.user, self.terminal),
            {
                'can_view_terminal': True,
                'can_edit_terminal': True,
                'can_manage_berth_plan': True,
            },
        )

    def test_workspace_navigation_uses_browser_and_canonical_berth_plan_route(self):
        self.client.force_login(self.user)
        editor = self.client.get(reverse('terminal_editor', args=[self.terminal.id]))
        plan = self.client.get(reverse('berth_plan', args=[self.terminal.id]))
        legacy_plan = self.client.get(reverse('berth_plan_legacy', args=[self.terminal.id]))
        che_editor = self.client.get(reverse('che_editor', args=[self.terminal.id]))

        self.assertContains(editor, reverse('terminal_browser'))
        self.assertNotContains(editor, reverse('berth_plan', args=[self.terminal.id]))
        self.assertContains(editor, 'Access policies')
        self.assertContains(editor, 'Operating scopes')
        self.assertContains(editor, 'Routing connection')
        self.assertContains(editor, 'Location and map frame')
        self.assertContains(editor, 'Civil infrastructure')
        self.assertContains(editor, 'Equipment placement')
        self.assertContains(editor, reverse('che_editor', args=[self.terminal.id]))
        self.assertContains(editor, 'terminal_editor_map_tools.js')
        html = editor.content.decode()
        helper_version = re.search(r'terminal_editor_map_tools\.js\?v=([^"\s]+)', html)
        editor_version = re.search(r'terminal_editor\.js\?v=([^"\s]+)', html)
        self.assertIsNotNone(helper_version)
        self.assertIsNotNone(editor_version)
        self.assertEqual(helper_version.group(1), editor_version.group(1))
        self.assertContains(editor, 'data-create-kind="yard_block"')
        self.assertContains(editor, 'data-create-kind="road"')
        self.assertContains(editor, 'id="basemapStyle"')
        self.assertContains(editor, 'id="assetForm" class="section context-asset-form" novalidate')
        self.assertContains(editor, 'id="assetContextPanel"')
        self.assertContains(editor, 'id="standaloneTransferFields"')
        self.assertContains(editor, 'id="bollardCount"')
        self.assertContains(editor, 'id="waterSide"')
        self.assertContains(editor, 'id="bollardDirection"')
        self.assertContains(editor, 'id="gateInLanes"')
        self.assertContains(editor, 'id="gateOutLanes"')
        self.assertContains(editor, 'id="railTrackCount"')
        self.assertContains(editor, 'id="quayServiceLanes"')
        self.assertContains(editor, 'id="quayCraneLanes"')
        self.assertContains(editor, 'id="quayBackreachLanes"')
        self.assertContains(editor, 'id="connectInterface"')
        self.assertContains(editor, 'id="scopeServiceLane"')
        self.assertNotContains(editor, '<label for="edgeCode">Code</label>')
        self.assertContains(editor, 'id="railTrackLength"')
        self.assertContains(editor, 'id="transferTopEnabled"')
        self.assertContains(editor, 'id="transferBottomEnabled"')
        self.assertContains(editor, 'id="transferLeftEnabled"')
        self.assertContains(editor, 'id="transferRightEnabled"')
        self.assertContains(editor, 'id="transferLrSplit"')
        self.assertContains(editor, 'id="transferTbSplit"')
        self.assertContains(plan, reverse('terminal_browser'))
        self.assertContains(plan, reverse('terminal_editor', args=[self.terminal.id]))
        self.assertContains(che_editor, reverse('terminal_editor', args=[self.terminal.id]))
        self.assertContains(che_editor, 'Component-specific configuration')
        self.assertContains(che_editor, 'id="detailRailAxis"')
        self.assertContains(che_editor, 'id="detailRailPosition"')
        self.assertContains(che_editor, 'id="detailRailMin"')
        self.assertContains(che_editor, 'id="detailRailMax"')
        self.assertEqual(legacy_plan.status_code, 200)

    def test_direct_workspace_urls_enforce_capabilities(self):
        self.client.force_login(self.user)
        denied = {
            'can_view_terminal': True,
            'can_edit_terminal': False,
            'can_manage_berth_plan': False,
        }
        with patch('minitos_core.views.terminal_capabilities', return_value=denied):
            editor = self.client.get(reverse('terminal_editor', args=[self.terminal.id]))
            plan = self.client.get(reverse('berth_plan', args=[self.terminal.id]))
            che_editor = self.client.get(reverse('che_editor', args=[self.terminal.id]))

        self.assertEqual(editor.status_code, 403)
        self.assertEqual(plan.status_code, 403)
        self.assertEqual(che_editor.status_code, 403)
