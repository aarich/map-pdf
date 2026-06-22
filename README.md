# Map PDF Generator

Script generates PDFs of a GPX file, plotting the route and the elevation profile. Specify mileage cutoffs to generate a separate PDF for each day.

## Usage

Create a virtual environment and install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Generate:

```bash
python generate.py --file my-trail.gpx --cutoffs 9.5,22.4,45.1
```

## Example

To generate the [example](/example/route.pdf):

```
python generate.py -f example/route.gpx -c 6.4,20,29 -b 1 -z 14
```

[![example](/example/screenshot.png)](/example/route.pdf)
