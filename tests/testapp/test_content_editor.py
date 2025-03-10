from django.contrib import admin
from django.contrib.auth.models import User
from django.core import checks
from django.db import models
from django.test import TestCase
from django.test.utils import isolate_apps
from django.urls import reverse
from testapp.models import Article, Download, Page, PageText, RichText

from content_editor.admin import ContentEditor, ContentEditorInline
from content_editor.contents import contents_for_item


class ContentEditorTest(TestCase):
    def login(self):
        u = User(username="test", is_active=True, is_staff=True, is_superuser=True)
        u.set_password("test")
        u.save()
        self.assertTrue(self.client.login(username="test", password="test"))

    def test_stuff(self):
        # Smoke test some stuff
        article = Article.objects.create(title="Test")
        richtext = article.testapp_richtext_set.create(
            text="<p>bla</p>", region="main", ordering=10
        )
        article.testapp_download_set.create(file="bla.pdf", region="main", ordering=20)

        with self.assertNumQueries(0):
            self.assertEqual(
                "%s" % richtext,
                f"testapp.RichText<region=main ordering=10 pk={richtext.pk}>",
            )

        with self.assertNumQueries(2):  # Two content types.
            contents = contents_for_item(article, plugins=[RichText, Download])

            self.assertEqual(contents.main[0], richtext)
            self.assertEqual(contents.main[0].parent, article)

        self.assertEqual(len(contents.main), 2)
        self.assertEqual(len(contents.sidebar), 0)
        self.assertEqual(len(contents.bla), 0)  # No AttributeError

        response = self.client.get(article.get_absolute_url())
        self.assertContains(response, "<h1>Test</h1>")
        self.assertContains(response, "<p>bla</p>")

        # Test for Contents.__iter__
        contents = contents_for_item(article, plugins=[RichText, Download])
        self.assertFalse(contents._sorted)
        self.assertEqual(len(list(contents)), 2)
        self.assertTrue(contents._sorted)
        self.assertEqual(len(list(contents)), 2)

        # Contents.__len__ also means that a Contents instance may be falsy
        self.assertEqual(len(contents), 2)
        self.assertTrue(contents)
        self.assertFalse(contents_for_item(article, []))

    def test_admin(self):
        self.login()
        response = self.client.get(reverse("admin:testapp_article_add"))

        self.assertContains(response, '_editor.js" data-context="{&quot;', 1)
        self.assertContains(response, 'id="content-editor-context"></sc', 1)
        self.assertContains(response, 'class="richtext"', 1)
        self.assertContains(
            response,
            "&quot;prefix&quot;: &quot;testapp_richtext_set&quot;",
            # 2,  (Once by us, and once by inlines.js)
        )

        article = Article.objects.create(title="Test")

        response = self.client.get(
            reverse("admin:testapp_article_change", args=(article.pk,))
        )
        self.assertContains(response, 'value="Test"', 1)

    def test_empty(self):
        article = Article.objects.create(title="Test")

        with self.assertNumQueries(2):
            contents = contents_for_item(article, plugins=[RichText, Download])

        self.assertEqual(contents.main, [])

        with self.assertNumQueries(0):
            contents = contents_for_item(article, plugins=[])

        self.assertEqual(contents.main, [])

    def test_unknown_regions(self):
        article = Article.objects.create(title="Test")
        for idx, region in enumerate(("", "notexists", "main")):
            RichText.objects.create(
                parent=article, ordering=idx, region=region, text="Test"
            )

        contents = contents_for_item(article, plugins=[RichText])
        self.assertEqual(len(contents._unknown_region_contents), 2)

    def test_inheritance(self):
        page = Page.objects.create(title="root")
        child = page.children.create(title="child 1")

        with self.assertNumQueries(1):
            contents = contents_for_item(child, plugins=[PageText], inherit_from=[page])
            self.assertEqual(contents.main, [])
            self.assertEqual(contents.sidebar, [])

        page.testapp_pagetext_set.create(
            region="main", ordering=10, text="page main text"
        )
        page.testapp_pagetext_set.create(
            region="sidebar", ordering=20, text="page sidebar text"
        )

        with self.assertNumQueries(1):
            contents = contents_for_item(child, plugins=[PageText], inherit_from=[page])
            self.assertEqual(contents.main, [])
            self.assertEqual([c.text for c in contents.sidebar], ["page sidebar text"])

            self.assertEqual(contents.sidebar[0].parent, page)

        child.testapp_pagetext_set.create(
            region="sidebar", ordering=10, text="child sidebar text"
        )

        child.testapp_pagetext_set.create(
            region="main", ordering=20, text="child main text"
        )

        with self.assertNumQueries(1):
            contents = contents_for_item(child, plugins=[PageText], inherit_from=[page])
            self.assertEqual([c.text for c in contents.main], ["child main text"])
            self.assertEqual([c.text for c in contents.sidebar], ["child sidebar text"])

            self.assertEqual(contents.sidebar[0].parent, child)

        response = self.client.get(child.get_absolute_url())
        self.assertContains(response, "child main text")
        self.assertContains(response, "child sidebar text")

    @isolate_apps()
    def test_model_checks(self):
        class Model(models.Model):
            name = models.CharField()

        class CustomAdminSite(admin.AdminSite):
            pass

        class ModelAdmin(ContentEditor):
            model = Model
            inlines = []

        site = CustomAdminSite()
        site.register(Model, ModelAdmin)

        self.assertEqual(
            site._registry[Model].check(),
            [
                checks.Error(
                    "ContentEditor models require a non-empty 'regions'"
                    " attribute or property.",
                    obj=ModelAdmin,
                    id="content_editor.E002",
                )
            ],
        )

    def test_inline_checks(self):
        self.assertEqual(admin.site._registry[Article].check(), [])

        class CustomAdminSite(admin.AdminSite):
            pass

        class RichTextInline(ContentEditorInline):
            model = RichText
            # Purposefully construct an inline with missing region
            # and ordering fields
            fieldsets = [(None, {"fields": ("text",)})]

        class ArticleAdmin(ContentEditor):
            model = Article
            inlines = [RichTextInline]

        site = CustomAdminSite()
        site.register(Article, ArticleAdmin)

        self.assertEqual(
            site._registry[Article].check(),
            [
                checks.Error(
                    "fieldsets must contain both 'region' and 'ordering'.",
                    obj=RichTextInline,
                    id="content_editor.E001",
                )
            ],
        )
