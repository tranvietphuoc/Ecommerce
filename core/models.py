from core import login_manager
from werkzeug.security import check_password_hash
from flask import current_app, flash, redirect, url_for, jsonify
from flask_login import UserMixin
from itsdangerous import TimedJSONWebSignatureSerializer as Serializer
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from core.search import add_to_index, remove_from_index, query_index


db = SQLAlchemy()


class SearchableMixin:
    """Use for elasticsearch."""

    @classmethod
    def search(cls, expression, page, per_page):
        """Search in database."""
        ids, total = query_index(cls.__tablename__, expression, page, per_page)
        if total == 0:
            return cls.query.filter_by(id=0), 0
        when = []
        for i in range(len(ids)):
            when.append((ids[i], i))

        return (
            cls.query.filter(cls.id.in_(ids)).order_by(db.case(when, value=cls.id)),
            total,
        )

    @classmethod
    def before_commit(cls, session):
        """Before commit."""
        session._changes = {
            "add": list(session.new),
            "update": list(session.dirty),
            "delete": list(session.deleted),
        }

    @classmethod
    def after_commit(cls, session):
        """After commit."""
        for obj in session._changes["add"]:
            if isinstance(obj, SearchableMixin):
                add_to_index(obj.__tablename__, obj)
        for obj in session._changes["update"]:
            if isinstance(obj, SearchableMixin):
                add_to_index(obj.__tablename__, obj)
        for obj in session._changes["delete"]:
            if isinstance(obj, SearchableMixin):
                remove_from_index(obj.__tablename__, obj)
        session._changes = None

    @classmethod
    def reindex(cls):
        """Re-index."""
        for obj in cls.query:
            add_to_index(cls.__tablename__, obj)


db.event.listen(db.session, "before_commit", SearchableMixin.before_commit)
db.event.listen(db.session, "after_commit", SearchableMixin.after_commit)


# this decorater is used to handle session
@login_manager.user_loader
def load_user(user_id):
    """
    Flask-Login keeps track of the logged in user by storing
    its unique identifier in Flask's user session,
    a storage space assigned to each user who connects to the application.
    Each time the logged-in user navigates to a new page,
    Flask-Login retrieves the ID of the user from the session,
    and then loads that user into memory.
    """

    return User.query.get(user_id)


@login_manager.request_loader
def load_request(request):
    user_name = request.form.get("user_name")
    user = User.query.filter_by(user_name=user_name).first()
    return user if user else None


@login_manager.unauthorized_handler
def unauthorized():
    flash("You must be logged in.")
    return redirect(url_for("users.login"))


# define an auxiliary table in relationship with Role and User model
# many-to-many relationship
users_roles = db.Table(
    "users_roles",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("role.id"), primary_key=True),
)


class Role(db.Model):
    """Roles of users"""

    # must have __tablename__ with lower case
    __tablename__ = "role"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    role_name = db.Column(db.String(30), unique=True)

    def __repr__(self):
        return f"<Role('{self.id}', '{self.role_name}')>"


class User(db.Model, UserMixin):
    """User table"""

    # must have __tablename__ with lower case
    __tablename__ = "user"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    user_name = db.Column(db.String(20), nullable=False, unique=True)
    full_name = db.Column(db.String(60))
    password = db.Column(db.String(200), nullable=False)
    address = db.Column(db.String(200), default="")
    city = db.Column(db.String(20), default="")
    state = db.Column(db.String(20), default="")
    country = db.Column(db.String(20), default="")
    zipcode = db.Column(db.String(20), default="")
    email = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False, unique=True)
    profile_picture = db.Column(db.String(200), nullable=False, default="default.jpg")
    is_superuser = db.Column(db.Boolean, default=False)
    roles = db.relationship(
        "Role",
        secondary=users_roles,
        primaryjoin=(users_roles.c.user_id == id),
        secondaryjoin=None,
        backref=db.backref("users", lazy="dynamic"),
    )

    def __repr__(self):
        return (
            f"<User('{self.id}', '{self.user_name}', '{self.email}', '{self.phone}')>"
        )

    def __str__(self):
        return f"User: {self.user_name}"

    @property
    def serialize(self):
        return jsonify({"name": self.first_name + self.last_name})

    def get_id(self):
        """Method to get user_id to pass into login_user(). Must be a function"""
        return self.id

    def has_role(self, *args):
        set_args = {arg for arg in args}  # set comprehenstion
        return set_args.issubset({role.role_name for role in self.roles})

    # define a method to get reset token, life time is 30 minutes
    def get_reset_token(self, expire_secs=1800):
        """
        Send a verify token to user's email.
        Use TimedJSONWebSignatureSerializer of itsdangerous module
        """
        s = Serializer(current_app.config["SECRET_KEY"], expire_secs)
        return s.dumps({"user_id": self.id}).decode("utf-8")

    @staticmethod
    def verify_password(form):
        """utility for check login password"""
        user = User.query.filter_by(email=form.email.data).first()

        if user:
            return check_password_hash(user.password, form.password.data)

    @staticmethod
    def verify_reset_token(token):
        """utility for verify reset password token"""
        s = Serializer(current_app.config["SECRET_KEY"])
        try:
            user_id = s.loads(token)["user_id"]
        except:
            return None
        return User.query.get(user_id)


# An auxiliary table to perform many-to-many relationship
# between Product and Category
products_categories = db.Table(
    "products_categories",
    db.Column("product_id", db.Integer, db.ForeignKey("product.id"), primary_key=True,),
    db.Column(
        "category_id", db.Integer, db.ForeignKey("category.id"), primary_key=True,
    ),
)


class Product(SearchableMixin, db.Model):
    """Product table. Has many-to-many with Category table."""

    __tablename__ = "product"
    __table_args__ = {"extend_existing": True}
    __searchable__ = ["product_name"]

    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(20), nullable=False)
    product_name = db.Column(db.String(100), nullable=False)
    product_description = db.Column(db.String(100), nullable=False)
    product_price = db.Column(db.DECIMAL, nullable=True)
    discounted_price = db.Column(db.DECIMAL)
    product_rating = db.Column(db.DECIMAL)
    product_review = db.Column(db.String(100), nullable=True)
    product_image = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    # many-to-one relationship
    # ForeignKey with Category table
    # category_id = db.Column(
    #    db.Integer, db.ForeignKey("category.category_id"), nullable=False
    # )
    # many-to-many relationship with Category table
    categories = db.relationship(
        "Category",
        secondary=products_categories,
        primaryjoin=(products_categories.c.product_id == id),
        secondaryjoin=None,
        backref=db.backref("products", lazy="dynamic"),
    )

    def __repr__(self):
        return f"<Product('{self.id}', '{self.product_name}', '{self.sku}',\
        '{self.product_image}', '{self.quantity}', '{self.product_price}',\
        '{self.discounted_price}', '{[c for c in self.categories]}')>"

    def __str__(self):
        return f"Product: {self.product_name} - quantity: {self.quantity}"


class Category(SearchableMixin, db.Model):
    """Category table. Has many-to-many relationship with Product table."""

    __tablename__ = "category"
    __table_args__ = {"extend_existing": True}
    __searchable__ = ["category_name"]

    id = db.Column(db.Integer, primary_key=True)
    category_name = db.Column(db.String(100), nullable=False)
    date_created = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # one to many relationship with Product table
    # products = db.relationship(
    #     "Product", backref=db.backref("category", lazy="select")
    # )

    def __repr__(self):
        return f"<Category('{self.id}', '{self.category_name}')>"

    def __str__(self):
        return f"Category: {self.category_name}"


class Cart(db.Model):
    """Cart table"""

    __tablename__ = "cart"
    __table_args__ = {"extend_existing": True}

    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, primary_key=True,
    )
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=True)
    quantity = db.Column(db.Integer, nullable=True)

    def __repr__(self):
        return f"<Cart('{self.user_id}', '{self.product_id}',\
            '{self.quantity}')>"


class Order(db.Model):
    """Order table"""

    __tablename__ = "order"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    total_price = db.Column(db.DECIMAL, nullable=False)
    ordered_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<Order('{self.id}', '{self.order_date}',\
        '{self.total_price}', '{self.user_id}'>"

    def __str__(self):
        return f"Order: {self.order_id} on {self.order_date}"


class OrderedProduct(db.Model):
    """Ordered product table"""

    __tablename__ = "orderedproduct"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    def __repr__(self):
        return f"<OrderProduct('{self.id}', '{self.order_id}',\
        '{self.product_id}', '{self.quantity}')>"


class SaleTransaction(db.Model):
    """Sale transaction table"""

    __tablename__ = "saletransaction"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    transaction_date = db.Column(db.DateTime, nullable=False)
    amount = db.Column(db.DECIMAL, nullable=False)
    cc_number = db.Column(db.String(50), nullable=False)
    cc_type = db.Column(db.String(50), nullable=False)
    response = db.Column(db.String(50), nullable=False)
    response = db.Column(db.String(50), nullable=False)

    def __repr__(self):
        return f"<OrderTransaction('{self.id}', '{self.order_id}',\
        '{self.amount}', '{self.cc_number}', '{self.cc_type}',\
        '{self.response}', '{self.transaction_date}')>"

    def __str__(self):
        return f"Transaction: {self.id} on {self.transaction_date}"
