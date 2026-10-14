# One typed key/value pair. Exactly one *_value field is meaningful, selected by `type`.
uint8 TYPE_STRING=0
uint8 TYPE_INT=1
uint8 TYPE_DOUBLE=2
uint8 TYPE_BOOL=3

string key
uint8 type
string string_value
int64 int_value
float64 double_value
bool bool_value
