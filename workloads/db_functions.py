from django.contrib.postgres.fields import DateTimeRangeField
from django.db.models import Func


class AvailabilityRange(Func):
    """A fixed-date half-open time range, used only for availability integrity."""
    output_field = DateTimeRangeField()

    def as_sql(self, compiler, connection, **extra_context):
        start, start_params = compiler.compile(self.source_expressions[0])
        end, end_params = compiler.compile(self.source_expressions[1])
        return f"tsrange(DATE '2000-01-01' + {start}, DATE '2000-01-01' + {end}, '[)')", [*start_params, *end_params]
