(matching)=
# {fa}`equals,style=fas` Request Matching
Requests are matched according to the request method, URL, parameters and body. All of these values
are normalized to account for any variations that do not modify response content.

There are some additional options to configure how you want requests to be matched.

## Matching Request Headers
In some cases, different request header values may result in different response data, so you may
want to cache them separately. You can specify which headers you want to match with the
`match_headers` option:
```python
>>> session = CachedSession(match_headers=['Accept'])
>>> # Each of these requests will be sent and cached separately
>>> session.get('https://httpbin.org/headers', {'Accept': 'text/plain'})
>>> session.get('https://httpbin.org/headers', {'Accept': 'application/json'})
```

If you want to match _all_ request headers, you can pass `match_headers=True`.


(filter-params)=
## Selective Parameter Matching
By default, all normalized request parameters are matched. In some cases, there may be request
parameters that don't affect the response data, for example authentication tokens or credentials.
If you want to ignore specific parameters, specify them with the `ignored_parameters` option.

**Request Parameters:**

In this example, only the first request will be sent, and the second request will be a cache hit
due to the ignored parameters:
```python
>>> session = CachedSession(ignored_parameters=['auth-token'])
>>> session.get('https://httpbin.org/get', params={'auth-token': '2F63E5DF4F44'})
>>> r = session.get('https://httpbin.org/get', params={'auth-token': 'D9FAEB3449D3'})
>>> assert r.from_cache is True
```

**Request Body Parameters:**

This also applies to parameters in a JSON-formatted request body:
```python
>>> session = CachedSession(allowable_methods=('GET', 'POST'), ignored_parameters=['auth-token'])
>>> session.post('https://httpbin.org/post', json={'auth-token': '2F63E5DF4F44'})
>>> r = session.post('https://httpbin.org/post', json={'auth-token': 'D9FAEB3449D3'})
>>> assert r.from_cache is True
```

**Request Headers:**

As well as headers, if `match_headers` is also used:
```python
>>> session = CachedSession(ignored_parameters=['auth-token'], match_headers=True)
>>> session.get('https://httpbin.org/get', headers={'auth-token': '2F63E5DF4F44'})
>>> r = session.get('https://httpbin.org/get', headers={'auth-token': 'D9FAEB3449D3'})
>>> assert r.from_cache is True
```
```{note}
Since `ignored_parameters` is most often used for sensitive info like credentials, these values will also be removed from the cached request parameters, body, and headers.
```

(custom-matching)=
## Custom Request Matching
If you need more advanced behavior, you can implement your own custom request matching.

Request matching is accomplished using a **cache key**, which uniquely identifies a response in the
cache based on request info. For example, the option `ignored_parameters=['foo']` works by excluding
the `foo` request parameter from the cache key, meaning these three requests will all use the same
cached response:
```python
>>> session = CachedSession(ignored_parameters=['foo'])
>>> response_1 = session.get('https://example.com')          # cache miss
>>> response_2 = session.get('https://example.com?foo=bar')  # cache hit
>>> response_3 = session.get('https://example.com?foo=qux')  # cache hit
>>> assert response_1.cache_key == response_2.cache_key == response_3.cache_key
```

If you want to implement your own request matching, you can provide a cache key function which will
take a {py:class}`~requests.PreparedRequest` plus optional keyword args, and return a string:
```python
def create_key(request: requests.PreparedRequest, **kwargs) -> str:
    """Generate a custom cache key for the given request"""
```

You can then pass this function via the `key_fn` param:
```python
session = CachedSession(key_fn=create_key)
```

`**kwargs` includes relevant {py:class}`.BaseCache` settings and any other keyword args passed to
{py:meth}`.CachedSession.send()`. If you want use a custom matching function _and_ the existing
options `ignored_parameters` and `match_headers`, you can implement them in `key_fn`:
```python
def create_key(
    request: requests.PreparedRequest,
    ignored_parameters: List[str] = None,
    match_headers: List[str] = None,
    **kwargs,
) -> str:
    """Generate a custom cache key for the given request"""
```

See {py:func}`.create_key` for the reference implementation, and see the rest of the
{py:mod}`.cache_keys` module for some potentially useful helper functions.


```{tip}
See {ref}`Examples<custom_keys>` page for a complete example for custom request matching.
```
```{tip}
As a general rule, if you include less information in your cache keys, you will have more cache hits
and use less storage space, but risk getting incorrect response data back.
```
```{warning}
If you provide a custom key function for a non-empty cache, any responses previously cached with a
different key function will be unused, so it's recommended to clear the cache first.
```
