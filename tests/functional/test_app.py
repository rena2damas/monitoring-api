class TestApp:

    def test_can_create_app(self, app):
        """Ensure app is created."""
        assert app is not None

    def test_redirect_root_to_application_root(self, client, url_prefix):
        """Ensure root path is redirected to application root."""
        response = client.get("/")
        assert response.status_code == 302

        response = client.get('/', follow_redirects=True)
        request = response.request
        assert response.status_code == 200
        assert request.path.rstrip('/') == url_prefix

    def test_swagger_apidocs(self, client, url_prefix):
        """Ensure app serves swagger specs."""
        response = client.get(f"{url_prefix}/swagger.json")

        assert response.status_code == 200