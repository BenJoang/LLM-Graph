# Input format

The helper accepts either a top-level array of JSON objects:

```json
[
  {"name": "alpha", "status": "active"},
  {"name": "beta", "status": "paused"}
]
```

or an object containing that array under `items`:

```json
{"items": [{"name": "alpha", "status": "active"}]}
```

`--group-by` names a top-level field. Records missing that field are counted in
`missing_group_field`; nested field paths are intentionally unsupported in this
minimal example.
